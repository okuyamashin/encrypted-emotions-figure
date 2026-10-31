# encrypted-emotions-figure

3D order-book terrain figure used by **Encrypted Emotions**.

Absolute-price “ceiling river” visualization: split bid/ask banks, flat water (spread), volume gates, JST/price grids, layer toggles, and floor overlays (mid / SMA / Bollinger / volume).

## License

[MIT](./LICENSE) — Copyright (c) 2026 okuyama.

You may use, modify, and redistribute this figure code (including in Encrypted Emotions and other projects) under the MIT License.

## Quick look

Open the demo in a local static server (ES modules):

```bash
cd viz
python3 -m http.server 8765
# http://127.0.0.1:8765/orderbook-terrain-3d.html
```

## Rebuild from snapshots

Expects bitFlyer board/ticker JSON trees under `data/raw/crypto/bitflyer/` (and optional USDJPY under `data/raw/ref/yahoo/usdjpy/`), same path layout the collector uses.

```bash
python3 scripts/build_orderbook_terrain.py --from 2026-10-31T08:30 --to 2026-10-31T10:30
```

Outputs:

- `data/viz/terrain-bitflyer.json`
- `data/viz/orderbook-terrain-3d.html`
- `viz/orderbook-terrain-3d.html`

Template: `scripts/templates/orderbook-terrain-3d.template.html`  
Example payload: `examples/terrain-bitflyer.json`

## Credits

- Rendering: [Three.js](https://threejs.org/) (MIT) via import map / CDN
- Sample market geometry in the demo is derived from public exchange snapshots for illustration
