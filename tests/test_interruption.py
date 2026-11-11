"""Interruption de la voix (assistant/static/interruption.js), simulée avec Node si présent.

On fabrique la voix de l'assistant, l'écho qui revient dans le micro (avec retard et variations),
et parfois ta voix par-dessus : il ne doit jamais se couper sur son propre écho ni sur un bruit
bref, ni sur les bouffées d'écho que l'annulation du navigateur laisse passer, et doit se taire
en à peine plus d'une seconde quand tu parles nettement plus fort que son écho.
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
// fuite : bouffées de sa voix que l'annulation d'écho du navigateur laisse passer (gain appris minuscule)
function essai({echo, toi = 0, debutToi = 3000, bruit = 0, graine, gain = 0.5, fuite = 0}) {
  const h = hasard(graine), env = voix(h), d = I.creerDetecteur({gain});
  let volume = 1, baisse = null;   // l'appelant baisse sa voix pendant la vérification
  for (let t = 0; t < 8000; t += 16) {
    const s = t / 1000;
    const v = baisse !== null && t - baisse > 60 ? 0.1 : 1;   // le son déjà parti met ~60 ms à s'éteindre
    const fuit = fuite && (t % 2500) > 1200 && (t % 2500) < 2000 ? fuite : 0;
    const e = v * (echo + fuit) * I.niveauA(env, s - 0.12) * (0.6 + 0.5 * h());
    const u = toi && t >= debutToi ? toi * ((t - debutToi) % 250 < 180 ? 0.7 + 0.6 * h() : 0.2) : 0;
    const b = bruit && t > 3000 && t < 3180 ? bruit : 0;
    const rms = Math.sqrt(e * e + u * u + b * b + 9e-6);
    const r = d.image(rms, rms * 3, volume * I.niveauA(env, s), t);
    if (r === 'verifier') { baisse = t; volume = 0.1; }
    if (r === 'echo') { baisse = null; volume = 1; }
    if (r === 'coupe') return t;
  }
  return null;
}
const r = {fauxEcho: 0, fauxBruit: 0, fauxFuite: 0, latences: [], latencesFuite: []};
for (let g = 1; g <= 20; g++) {
  for (const echo of [0.05, 0.3, 1, 2]) if (essai({echo, graine: g}) !== null) r.fauxEcho++;
  if (essai({echo: 0.3, gain: 0.05, graine: g}) !== null) r.fauxEcho++;   // gain d'écho mal connu au départ
  if (essai({echo: 0.1, bruit: 0.3, graine: g}) !== null) r.fauxBruit++;
  // annulation d'écho : écho de 2 %, mais des bouffées à 60 % (cas réel : phrases longues coupées)
  for (const fuite of [0.3, 0.6, 1]) if (essai({echo: 0.02, gain: 0.02, fuite, graine: g}) !== null) r.fauxFuite++;
  const c = essai({echo: 0.1, toi: 0.06, graine: g});
  r.latences.push(c === null ? null : c - 3000);
  const cf = essai({echo: 0.02, gain: 0.02, fuite: 0.6, toi: 0.05, debutToi: 1500, graine: g});
  r.latencesFuite.push(cf === null ? null : cf - 1500);
}
console.log(JSON.stringify(r));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js absent")
def test_interruption_sans_se_couper_sur_son_echo():
    sortie = subprocess.run(["node", "-e", SIMULATION, str(JS)], capture_output=True, text=True, check=True)
    r = json.loads(sortie.stdout)
    assert r["fauxEcho"] == 0
    assert r["fauxBruit"] == 0
    assert r["fauxFuite"] == 0   # bouffées d'écho que l'annulation du navigateur laisse passer
    # il baisse sa voix ~0,5 s après ton premier mot et se tait ~0,4 s plus tard (vérification)
    assert all(x is not None and x < 1100 for x in r["latences"]), r["latences"]
    assert all(x is not None and x < 1200 for x in r["latencesFuite"]), r["latencesFuite"]
