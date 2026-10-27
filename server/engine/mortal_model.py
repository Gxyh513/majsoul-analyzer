"""Mortal neural network wrapper (PyTorch + libriichi).

Loads ``mortal.pth`` (versions 1/2/3/4) when available and evaluates batches of
``(obs, mask)`` tensors produced by ``libriichi.state.PlayerState.encode_obs``.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

# Ensure our local libriichi.pyd is importable
_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from libriichi.consts import ACTION_SPACE, obs_shape, oracle_obs_shape  # noqa: E402

log = logging.getLogger(__name__)


class ChannelAttention(nn.Module):
    def __init__(self, channels, ratio=16, actv_builder=nn.ReLU, bias=True):
        super().__init__()
        self.shared_mlp = nn.Sequential(
            nn.Linear(channels, channels // ratio, bias=bias),
            actv_builder(),
            nn.Linear(channels // ratio, channels, bias=bias),
        )
        if bias:
            for mod in self.modules():
                if isinstance(mod, nn.Linear):
                    nn.init.constant_(mod.bias, 0)

    def forward(self, x: Tensor):
        avg_out = self.shared_mlp(x.mean(-1))
        max_out = self.shared_mlp(x.amax(-1))
        weight = (avg_out + max_out).sigmoid()
        return weight.unsqueeze(-1) * x


class ResBlock(nn.Module):
    def __init__(self, channels, *, norm_builder=nn.Identity, actv_builder=nn.ReLU, pre_actv=False):
        super().__init__()
        self.pre_actv = pre_actv
        if pre_actv:
            self.res_unit = nn.Sequential(
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
            )
        else:
            self.res_unit = nn.Sequential(
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
                actv_builder(),
                nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
                norm_builder(),
            )
            self.actv = actv_builder()
        self.ca = ChannelAttention(channels, actv_builder=actv_builder, bias=True)

    def forward(self, x):
        out = self.ca(self.res_unit(x)) + x
        return out if self.pre_actv else self.actv(out)


class ResNet(nn.Module):
    def __init__(
        self,
        in_channels,
        conv_channels,
        num_blocks,
        *,
        norm_builder=nn.Identity,
        actv_builder=nn.ReLU,
        pre_actv=False,
    ):
        super().__init__()
        blocks = [
            ResBlock(conv_channels, norm_builder=norm_builder, actv_builder=actv_builder, pre_actv=pre_actv)
            for _ in range(num_blocks)
        ]
        layers = [nn.Conv1d(in_channels, conv_channels, kernel_size=3, padding=1, bias=False)]
        if pre_actv:
            layers += [*blocks, norm_builder(), actv_builder()]
        else:
            layers += [norm_builder(), actv_builder(), *blocks]
        layers += [
            nn.Conv1d(conv_channels, 32, kernel_size=3, padding=1),
            actv_builder(),
            nn.Flatten(),
            nn.Linear(32 * 34, 1024),
        ]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)


class Brain(nn.Module):
    def __init__(self, *, conv_channels, num_blocks, is_oracle=False, version=1):
        super().__init__()
        self.is_oracle = is_oracle
        self.version = version
        in_channels = obs_shape(version)[0]
        if is_oracle:
            in_channels += oracle_obs_shape(version)[0]

        norm_builder = partial(nn.BatchNorm1d, conv_channels, momentum=0.01)
        actv_builder = partial(nn.Mish, inplace=True)
        pre_actv = True

        match version:
            case 1:
                actv_builder = partial(nn.ReLU, inplace=True)
                pre_actv = False
                self.latent_net = nn.Sequential(nn.Linear(1024, 512), nn.ReLU(inplace=True))
                self.mu_head = nn.Linear(512, 512)
                self.logsig_head = nn.Linear(512, 512)
            case 2:
                pass
            case 3 | 4:
                norm_builder = partial(nn.BatchNorm1d, conv_channels, momentum=0.01, eps=1e-3)
            case _:
                raise ValueError(f"Unsupported Mortal version {version}")

        self.encoder = ResNet(
            in_channels=in_channels,
            conv_channels=conv_channels,
            num_blocks=num_blocks,
            norm_builder=norm_builder,
            actv_builder=actv_builder,
            pre_actv=pre_actv,
        )
        self.actv = actv_builder()

    def forward(self, obs: Tensor):
        phi = self.encoder(obs)
        if self.version == 1:
            latent_out = self.latent_net(phi)
            return self.mu_head(latent_out)
        return self.actv(phi)


class DQN(nn.Module):
    def __init__(self, *, version=1):
        super().__init__()
        self.version = version
        match version:
            case 1:
                self.v_head = nn.Linear(512, 1)
                self.a_head = nn.Linear(512, ACTION_SPACE)
            case 2 | 3:
                hidden_size = 512 if version == 2 else 256
                self.v_head = nn.Sequential(
                    nn.Linear(1024, hidden_size), nn.Mish(inplace=True), nn.Linear(hidden_size, 1)
                )
                self.a_head = nn.Sequential(
                    nn.Linear(1024, hidden_size), nn.Mish(inplace=True), nn.Linear(hidden_size, ACTION_SPACE)
                )
            case 4:
                self.net = nn.Linear(1024, 1 + ACTION_SPACE)
                nn.init.constant_(self.net.bias, 0)

    def forward(self, phi: Tensor, mask: Tensor):
        if self.version == 4:
            v, a = self.net(phi).split((1, ACTION_SPACE), dim=-1)
        else:
            v = self.v_head(phi)
            a = self.a_head(phi)
        a_sum = a.masked_fill(~mask, 0.0).sum(-1, keepdim=True)
        mask_sum = mask.sum(-1, keepdim=True).clamp(min=1)
        a_mean = a_sum / mask_sum
        q = (v + a - a_mean).masked_fill(~mask, -torch.inf)
        return q


# Minimal size threshold for a plausible full Mortal neural network checkpoint (10MB)
MIN_VALID_WEIGHT_SIZE = 10_000_000


class LoadedMortal:
    def __init__(self, pth_path: Path, device: str | None = None, enforce_v4_582500: bool = False):
        self.pth_path = Path(pth_path)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        # 1. Physical file checks
        if not self.pth_path.is_file():
            raise FileNotFoundError(f"Mortal 权重文件不存在: {self.pth_path}")
        file_size = self.pth_path.stat().st_size
        min_threshold = 100_000_000 if enforce_v4_582500 else 100_000
        if file_size < min_threshold:
            raise ValueError(
                f"Mortal 权重文件损坏或体积异常: {self.pth_path.name} (大小为 {file_size} 字节，小于有效阈值 {min_threshold} 字节)"
            )

        # 2. PyTorch checkpoint deserialization
        try:
            state = torch.load(self.pth_path, weights_only=False, map_location="cpu")
        except Exception as e:
            raise RuntimeError(f"解析 Mortal Checkpoint 失败，文件损坏或格式无效: {e}") from e

        if not isinstance(state, dict):
            raise ValueError("Mortal Checkpoint 内容格式异常，根对象不是字典映射")

        # 3. Mandatory checkpoint keys check
        required_keys = {"config", "mortal", "current_dqn"}
        missing_keys = required_keys - set(state.keys())
        if missing_keys:
            raise ValueError(f"Mortal Checkpoint 缺少关键结构字段: {missing_keys}")

        cfg = state["config"]
        if not isinstance(cfg, dict) or "control" not in cfg or "resnet" not in cfg:
            raise ValueError("Mortal Checkpoint 配置区 (config.control / config.resnet) 结构不完整")

        self.version: int = cfg["control"].get("version", 1)
        num_blocks = cfg["resnet"].get("num_blocks")
        conv_channels = cfg["resnet"].get("conv_channels")
        if not isinstance(num_blocks, int) or not isinstance(conv_channels, int):
            raise ValueError(f"Mortal 网络结构参数无效: num_blocks={num_blocks}, conv_channels={conv_channels}")

        steps = state.get("steps")
        self.steps = steps

        # 4. Strict identity and architecture verification for Mortal-V4-582500
        if enforce_v4_582500:
            if self.version != 4:
                raise ValueError(f"模型版本不匹配: 期望 Mortal-V4 (version=4)，实际检测到 V{self.version}")
            if num_blocks != 40 or conv_channels != 192:
                raise ValueError(
                    f"模型 ResNet 结构不匹配: 期望 40 blocks / 192 channels，实际为 {num_blocks} blocks / {conv_channels} channels"
                )
            if steps != 582500:
                raise ValueError(f"模型训练步数不匹配: 期望 steps=582500，实际检测为 steps={steps}")

        # 5. Tensor keys & shapes verification
        mortal_state = state["mortal"]
        dqn_state = state["current_dqn"]
        if not isinstance(mortal_state, dict) or not isinstance(dqn_state, dict):
            raise ValueError("Checkpoint 内 mortal 或 current_dqn 状态字典格式错误")

        # Verify first conv layer dimension
        if "encoder.net.0.weight" not in mortal_state:
            raise ValueError("Mortal 权重缺失关键首层卷积权重 encoder.net.0.weight")
        first_conv_shape = tuple(mortal_state["encoder.net.0.weight"].shape)
        expected_in_c = obs_shape(self.version)[0]
        if first_conv_shape != (conv_channels, expected_in_c, 3):
            raise ValueError(
                f"首层卷积维度不匹配: 期望 ({conv_channels}, {expected_in_c}, 3)，实际为 {first_conv_shape}"
            )

        # Verify DQN output layer dimension
        if self.version == 4:
            if "net.weight" not in dqn_state or "net.bias" not in dqn_state:
                raise ValueError("Mortal V4 DQN 状态字典缺失输出层 net.weight / net.bias")
            dqn_w_shape = tuple(dqn_state["net.weight"].shape)
            dqn_b_shape = tuple(dqn_state["net.bias"].shape)
            if dqn_w_shape != (1 + ACTION_SPACE, 1024) or dqn_b_shape != (1 + ACTION_SPACE,):
                raise ValueError(
                    f"Mortal V4 DQN 输出层形状异常: net.weight={dqn_w_shape}, net.bias={dqn_b_shape}"
                )

        # 6. Model Tag derivation
        if "tag" in state:
            self.tag = str(state["tag"])
        elif "timestamp" in state:
            ts = datetime.fromtimestamp(state["timestamp"], tz=timezone.utc).strftime("%y%m%d%H")
            self.tag = f"mortal-v{self.version}-b{num_blocks}c{conv_channels}-{ts}"
        else:
            self.tag = f"mortal-v{self.version}-b{num_blocks}c{conv_channels}"

        # 7. Model instantiation & strict state_dict loading
        try:
            self.brain = Brain(
                version=self.version, num_blocks=num_blocks, conv_channels=conv_channels
            ).to(self.device).eval()
            self.dqn = DQN(version=self.version).to(self.device).eval()
            self.brain.load_state_dict(mortal_state, strict=True)
            self.dqn.load_state_dict(dqn_state, strict=True)
        except Exception as e:
            raise RuntimeError(f"Mortal 神经网络参数加载或实例化失败 (strict=True): {e}") from e

        self.file_size = file_size
        log.info(
            "成功载入 Mortal-V%d 神经网络权重: %s (tag: %s, steps: %s, size: %.2fMB) 运行在 %s",
            self.version,
            self.pth_path.name,
            self.tag,
            self.steps,
            self.file_size / (1024 * 1024),
            self.device,
        )

    @torch.inference_mode()
    def eval_batch(self, obs_list: list[np.ndarray], mask_list: list[np.ndarray]) -> np.ndarray:
        if not obs_list:
            return np.zeros((0, ACTION_SPACE), dtype=np.float32)
        try:
            obs_t = torch.as_tensor(np.stack(obs_list, axis=0), dtype=torch.float32, device=self.device)
            mask_t = torch.as_tensor(np.stack(mask_list, axis=0), dtype=torch.bool, device=self.device)
            phi = self.brain(obs_t)
            q = self.dqn(phi, mask_t)
            res = q.cpu().numpy()
            return res
        except Exception as e:
            log.error("Mortal 神经网络批次推理执行异常: %s", e, exc_info=True)
            raise RuntimeError(f"Mortal 神经网络批次推理执行失败: {e}") from e
