"""Interruption de la voix (assistant/static/interruption.js), simulée avec Node si présent.

On fabrique la voix de l'assistant, l'écho qui revient dans le micro (avec retard et variations),
et parfois ta voix par-dessus : il ne doit jamais se couper sur son propre écho ni sur un bruit
bref, et doit se taire en moins d'une seconde quand tu parles nettement plus fort que son écho.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "assistant" / "static" / "interruption.js"

SIMULATION = r"""
require(process.argv[1]);
const I = globalThis.Interruption;
function hasard(s) { return () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }; }
// voix de l'assistant : syllabes de 150 ms, blancs de 60 ms
function voix(h) { const e = new Float32Array(800); for (let k = 0; k < e.length; k++) e[k] = k % 21 < 15 ? .08 + .1 * h() : .002; return e; }
function essai({echo, toi = 0, debutToi = 3000, bruit = 0, graine, gain = 0.5}) {
  const h = hasard(graine), env = voix(h), d = I.creerDetecteur({gain});
  for (let t = 0; t < 8000; t += 16) {
    const s = t / 1000;
    const e = echo * I.niveauA(env, s - 0.12) * (0.6 + 0.5 * h());
    const u = toi && t >= debutToi ? toi * ((t - debutToi) % 250 < 180 ? 0.7 + 0.6 * h() : 0.2) : 0;
    const b = bruit && t > 3000 && t < 3180 ? bruit : 0;
    const rms = Math.sqrt(e * e + u * u + b * b + 9e-6);
    if (d.image(rms, rms * 3, I.niveauA(env, s), t) === 'coupe') return t;
  }
  return null;
}
const r = {fauxEcho: 0, fauxBruit: 0, latences: []};
for (let g = 1; g <= 20; g++) {
  for (const echo of [0.05, 0.3, 1, 2]) if (essai({echo, graine: g}) !== null) r.fauxEcho++;
  if (essai({echo: 0.3, gain: 0.05, graine: g}) !== null) r.fauxEcho++;   // gain d'écho mal connu au départ
  if (essai({echo: 0.1, bruit: 0.3, graine: g}) !== null) r.fauxBruit++;
  const c = essai({echo: 0.1, toi: 0.06, graine: g});
  r.latences.push(c === null ? null : c - 3000);
}
console.log(JSON.stringify(r));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js absent")
def test_interruption_sans_se_couper_sur_son_echo():
    sortie = subprocess.run(["node", "-e", SIMULATION, str(JS)], capture_output=True, text=True, check=True)
    r = json.loads(sortie.stdout)
    assert r["fauxEcho"] == 0
    assert r["fauxBruit"] == 0
    assert all(x is not None and x < 1000 for x in r["latences"]), r["latences"]
