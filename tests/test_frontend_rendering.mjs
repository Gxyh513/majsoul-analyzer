import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => readFileSync(path.join(root, relativePath), "utf8");

test("ankan renders four slots in one meld group", () => {
  const context = {
    getTileBackSrc: () => "/tiles/back.svg",
    tileAssetSrc: (tile) => `/tiles/${tile}.svg`,
  };
  vm.runInNewContext(read("web/js/board-renderer.js"), context);
  const html = context.renderCallsHtml([{
    type: "ankan",
    pai: "5m",
    consumed: ["5m", "5m", "5m", "5m"],
    tiles: ["5m", "5m", "5m", "5m"],
    from: 0,
  }], 0, 0);

  assert.equal((html.match(/class="tileDiv meld-tile/g) || []).length, 4);
  assert.match(html, /meld-group--ankan/);
  assert.equal((html.match(/\/tiles\/back\.svg/g) || []).length, 2);
});

test("daiminkan renders four slots and exactly one called tile for all seats", () => {
  const context = {
    getTileBackSrc: () => "/tiles/back.svg",
    tileAssetSrc: (tile) => `/tiles/${tile}.svg`,
  };
  vm.runInNewContext(read("web/js/board-renderer.js"), context);

  for (let seat = 0; seat < 4; seat++) {
    for (const sourceOffset of [1, 2, 3]) {
      const from = (seat + sourceOffset) % 4;
      const html = context.renderCallsHtml([{
        type: "daiminkan",
        pai: "5p",
        consumed: ["5p", "5p", "5p"],
        tiles: ["5p", "5p", "5p", "5p"],
        from,
      }], seat, seat);
      assert.equal((html.match(/class="tileDiv meld-tile/g) || []).length, 4);
      assert.equal((html.match(/meld-tile--called/g) || []).length, 1);
    }
  }
});

test("kakan renders the added tile as a fourth in-group slot without float overlays", () => {
  const context = {
    getTileBackSrc: () => "/tiles/back.svg",
    tileAssetSrc: (tile) => `/tiles/${tile}.svg`,
  };
  vm.runInNewContext(read("web/js/board-renderer.js"), context);

  for (let seat = 0; seat < 4; seat++) {
    for (const sourceOffset of [1, 2, 3]) {
      const from = (seat + sourceOffset) % 4;
      const html = context.renderCallsHtml([{
        type: "kakan",
        pai: "5s",
        consumed: ["5s", "5s"],
        added: "5s",
        tiles: ["5s", "5s", "5s", "5s"],
        from,
      }], seat, seat);
      assert.equal((html.match(/class="tileDiv meld-tile/g) || []).length, 4);
      assert.equal((html.match(/meld-tile--called/g) || []).length, 1);
      assert.equal((html.match(/meld-tile--added/g) || []).length, 1);
      assert.match(html, /meld-group--kakan/);
      assert.doesNotMatch(html, /has-float|class="float"/);
    }
  }
});

test("chi and pon remain three-tile melds", () => {
  const context = {
    getTileBackSrc: () => "/tiles/back.svg",
    tileAssetSrc: (tile) => `/tiles/${tile}.svg`,
  };
  vm.runInNewContext(read("web/js/board-renderer.js"), context);

  for (const type of ["chi", "pon"]) {
    const html = context.renderCallsHtml([{
      type,
      pai: "3m",
      consumed: ["1m", "2m"],
      tiles: ["3m", "1m", "2m"],
      from: 1,
    }], 0, 0);
    assert.equal((html.match(/class="tileDiv meld-tile/g) || []).length, 3);
    assert.equal((html.match(/meld-tile--called/g) || []).length, 1);
  }
});

test("legacy playback speeds normalize to 2x and 10x has an 80ms interval", () => {
  const store = new Map([["majsoul_play_speed", "1.5"]]);
  const localStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, String(value)),
  };
  const context = {
    localStorage,
    setInterval: () => 1,
    clearInterval: () => {},
    $: () => null,
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(read("web/js/replay-state.js"), context);
  vm.runInContext(read("web/js/replay-controls.js"), context);

  assert.equal(context.state.playSpeed, 2);
  assert.equal(store.get("majsoul_play_speed"), "2");

  context.setPlaySpeed(1);
  assert.equal(context.state.playSpeed, 2);
  context.setPlaySpeed(5);
  assert.equal(context.state.playSpeed, 5);
  assert.equal(context.getPlayIntervalMs(), 160);
  context.setPlaySpeed(10);
  assert.equal(context.state.playSpeed, 10);
  assert.equal(context.getPlayIntervalMs(), 80);
});

test("the playback selector exposes only 2x, 3x, 5x and 10x", () => {
  const html = read("web/index.html");
  const options = [...html.matchAll(/<option value="([^"]+)"[^>]*>(.*?)<\/option>/g)]
    .map((match) => [match[1], match[2].trim()]);
  assert.deepEqual(options, [
    ["2", "2X"],
    ["3", "3X"],
    ["5", "5X"],
    ["10", "10X"],
  ]);
});
