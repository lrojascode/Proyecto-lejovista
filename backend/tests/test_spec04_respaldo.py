"""Spec 04 — Respaldo cifrado, verificación y restauración.

La frontera es el **drill operativo completo** —respaldo, cifrado, transferencia,
restauración sobre host limpio y comprobación funcional— y eso no lo sustituye
ninguna prueba unitaria: lo que falla en una restauración es siempre lo que no
está en el guion. Su evidencia vive fechada en `docs/infra/backups/drills/`.

Lo que SÍ es automatizable, y es lo que hay acá, son los modos de fallo que la
verificación diaria tiene que detectar. Se prueban inyectándolos, porque esperar
a que ocurran significa descubrirlos el día del incidente:

* no hay respaldo remoto
* el respaldo está corrupto o truncado
* el respaldo es más viejo que el RPO
* el respaldo está cifrado para otra clave
* la clave equivocada no descifra, y falla ruidosamente

Más las dos salvaguardas del procedimiento: la restauración se niega a tocar la
base de producción, y el respaldo se niega a correr si la clave privada está en
el mismo host que protege.
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts" / "backup"

pytestmark = pytest.mark.skipif(
    shutil.which("gpg") is None, reason="Requiere gpg (es lo que cifra el respaldo)."
)


@pytest.fixture
def entorno(tmp_path):
    """Un 'VPS' de mentira: clave de respaldo, destino remoto y variables."""
    # El llavero NO va bajo `tmp_path`: gpg-agent abre un socket unix dentro de
    # GNUPGHOME y la ruta de pytest se pasa del límite de 104 bytes que impone el
    # sistema, con lo que la generación de la clave falla con un error opaco.
    gnupg = Path(tempfile.mkdtemp(prefix="lvgpg-", dir="/tmp"))
    gnupg.chmod(0o700)
    (gnupg / "gpg-agent.conf").write_text("allow-loopback-pinentry\n")
    (gnupg / "gpg.conf").write_text("pinentry-mode loopback\n")

    env = {
        **os.environ,
        "GNUPGHOME": str(gnupg),
        "LV_BACKUP_REMOTE_DRIVER": "local",
        "LV_BACKUP_REMOTE_PATH": str(tmp_path / "remoto"),
        "LV_BACKUP_STATUS_FILE": str(tmp_path / "estado.json"),
        "LV_BACKUP_PREFIX": "lejovista",
        "LV_PGDATABASE": "lejovista",
        "LV_ALLOW_SECRET_KEY_PRESENT": "1",
    }

    subprocess.run(
        ["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
         "Respaldo Test <respaldo@test>", "default", "default", "never"],
        env=env, check=True, capture_output=True,
    )
    salida = subprocess.run(
        ["gpg", "--list-keys", "--with-colons", "respaldo@test"],
        env=env, check=True, capture_output=True, text=True,
    ).stdout
    fpr = next(l.split(":")[9] for l in salida.splitlines() if l.startswith("fpr:"))

    pub = tmp_path / "pubkey.asc"
    pub.write_bytes(subprocess.run(
        ["gpg", "--armor", "--export", fpr], env=env, check=True, capture_output=True
    ).stdout)
    env["LV_BACKUP_PUBKEY"] = str(pub)

    (tmp_path / "remoto").mkdir()
    yield {"env": env, "tmp": tmp_path, "fpr": fpr, "pub": pub}

    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, capture_output=True)
    shutil.rmtree(gnupg, ignore_errors=True)


def _correr(script: str, entorno, *args):
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        env=entorno["env"], capture_output=True, text=True,
    )


def _artefacto(entorno, nombre: str, contenido: bytes = b"contenido de prueba",
               pubkey: Path | None = None, con_suma: bool = True):
    """Deja un artefacto cifrado en el remoto, con el nombre pedido.

    Publica también su suma sha256 al lado, igual que `backup.sh`: es lo que
    permite detectar un truncado del cuerpo, que conserva la cabecera OpenPGP
    intacta.
    """
    import hashlib

    claro = entorno["tmp"] / "claro.bin"
    claro.write_bytes(contenido)
    destino = Path(entorno["env"]["LV_BACKUP_REMOTE_PATH"]) / nombre
    subprocess.run(
        ["gpg", "--batch", "--yes", "--trust-model", "always",
         "--recipient-file", str(pubkey or entorno["pub"]),
         "--encrypt", "--output", str(destino), str(claro)],
        env=entorno["env"], check=True, capture_output=True,
    )
    if con_suma:
        suma = hashlib.sha256(destino.read_bytes()).hexdigest()
        destino.with_name(nombre + ".sha256").write_text(f"{suma}  {nombre}\n")
    return destino


def _sello(desfase_horas: float = 0) -> str:
    return time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime(time.time() - desfase_horas * 3600)
    )


# --------------------------------------------------------------------------
# Criterios 5-6 · la verificación detecta los fallos que importan
# --------------------------------------------------------------------------

def test_c5a_sin_respaldo_remoto_la_verificacion_falla(entorno):
    """El caso más silencioso: el proceso dejó de correr y nadie se enteró."""
    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "no hay ningun respaldo" in res.stderr


def test_c5b_un_respaldo_corrupto_se_detecta(entorno):
    """Un artefacto truncado restaura igual de mal que uno ausente, pero se ve
    igual de bien en un listado."""
    destino = _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg")
    datos = destino.read_bytes()
    destino.write_bytes(datos[: len(datos) // 3])  # truncado

    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "esta truncado o corrupto" in res.stderr


def test_c5b2_sin_suma_publicada_el_truncado_se_detecta_por_la_cabecera(entorno):
    """Respaldos anteriores a que se publicara la suma: la comprobación cae a la
    cabecera OpenPGP. Detecta menos, y lo dice en vez de fingir que verificó."""
    destino = _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg", con_suma=False)
    destino.write_bytes(b"esto no es OpenPGP en absoluto")

    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "no es un artefacto OpenPGP legible" in res.stderr


def test_c5c_un_respaldo_vacio_se_detecta(entorno):
    destino = _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg")
    destino.write_bytes(b"")

    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "vacio" in res.stderr


def test_c5d_un_respaldo_mas_viejo_que_el_rpo_se_detecta(entorno):
    """La antigüedad sale del NOMBRE, no del mtime del archivo: los proveedores
    de objetos reescriben mtime al copiar, y con eso un respaldo viejo parece
    nuevo."""
    _artefacto(entorno, f"lejovista_{_sello(desfase_horas=48)}_full.tar.gpg")

    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "por encima del RPO" in res.stderr


def test_c5e_un_respaldo_cifrado_para_otra_clave_se_detecta(entorno, tmp_path):
    """Cifrado sin ser legible por la clave custodiada no es un respaldo: es un
    archivo que nadie puede abrir el día del incidente."""
    otro_home = Path(tempfile.mkdtemp(prefix="lvgpg2-", dir="/tmp"))
    otro_home.chmod(0o700)
    (otro_home / "gpg-agent.conf").write_text("allow-loopback-pinentry\n")
    (otro_home / "gpg.conf").write_text("pinentry-mode loopback\n")
    env_otro = {**entorno["env"], "GNUPGHOME": str(otro_home)}
    subprocess.run(
        ["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
         "Otra <otra@test>", "default", "default", "never"],
        env=env_otro, check=True, capture_output=True,
    )
    salida = subprocess.run(
        ["gpg", "--list-keys", "--with-colons", "otra@test"],
        env=env_otro, check=True, capture_output=True, text=True,
    ).stdout
    fpr_otro = next(l.split(":")[9] for l in salida.splitlines() if l.startswith("fpr:"))
    pub_otro = tmp_path / "otra_pub.asc"
    pub_otro.write_bytes(subprocess.run(
        ["gpg", "--armor", "--export", fpr_otro], env=env_otro, check=True,
        capture_output=True,
    ).stdout)
    # Se importa la ajena para poder cifrar con ella desde el llavero del "VPS".
    subprocess.run(["gpg", "--batch", "--import", str(pub_otro)],
                   env=entorno["env"], check=True, capture_output=True)

    _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg", pubkey=pub_otro)

    res = _correr("verify.sh", entorno)
    assert res.returncode != 0
    assert "no esta cifrado para la clave de respaldo custodiada" in res.stderr


def test_c5f_un_respaldo_sano_pasa_la_verificacion(entorno):
    """El par de los cinco anteriores: sin esto, un verificador que siempre
    falla también los pasaría todos."""
    _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg")

    res = _correr("verify.sh", entorno)
    assert res.returncode == 0, res.stderr
    assert "VERIFICACION OK" in res.stderr

    estado = Path(entorno["env"]["LV_BACKUP_STATUS_FILE"])
    assert estado.exists(), "la señal monitorizable tiene que quedar escrita"
    import json
    datos = json.loads(estado.read_text())
    assert datos["resultado"] == "ok"
    assert datos["rpo_horas"] == 24
    assert datos["antiguedad_horas"] == 0


def test_c5g_la_verificacion_escribe_la_senal_tambien_cuando_falla(entorno):
    """La observabilidad no puede depender de que todo vaya bien."""
    import json

    _correr("verify.sh", entorno)
    datos = json.loads(Path(entorno["env"]["LV_BACKUP_STATUS_FILE"]).read_text())
    assert datos["resultado"] == "fallo"
    assert datos["motivo"]


# --------------------------------------------------------------------------
# Criterios 2-3 · el cifrado protege, y la clave no vive en el VPS
# --------------------------------------------------------------------------

def test_c2_la_clave_equivocada_no_descifra_y_falla_ruidosamente(entorno, tmp_path):
    """Fallar limpio importa: un descifrado parcial produciría un archivo que
    parece un respaldo y no lo es."""
    destino = _artefacto(entorno, f"lejovista_{_sello()}_full.tar.gpg",
                         contenido=b"datos de los clientes")

    vacio = Path(tempfile.mkdtemp(prefix="lvgpg3-", dir="/tmp"))
    vacio.chmod(0o700)
    (vacio / "gpg.conf").write_text("pinentry-mode loopback\n")
    salida = tmp_path / "descifrado.bin"

    res = subprocess.run(
        ["gpg", "--batch", "--yes", "--decrypt", "--output", str(salida), str(destino)],
        env={**os.environ, "GNUPGHOME": str(vacio)}, capture_output=True, text=True,
    )
    assert res.returncode != 0, "sin la clave privada no puede descifrarse"
    assert not salida.exists() or salida.stat().st_size == 0, (
        "un descifrado fallido no puede dejar un archivo parcial"
    )
    assert b"datos de los clientes" not in destino.read_bytes(), (
        "el contenido no viaja en claro"
    )


def test_c3_el_respaldo_se_niega_si_la_clave_privada_esta_en_este_host(entorno):
    """Un respaldo cuya clave vive en el servidor que protege no protege del
    escenario que motiva cifrarlo."""
    env = {**entorno["env"]}
    env.pop("LV_ALLOW_SECRET_KEY_PRESENT", None)

    res = subprocess.run(
        ["bash", str(SCRIPTS / "verify.sh")], env=env, capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "clave PRIVADA de respaldo esta presente en este host" in res.stderr


# --------------------------------------------------------------------------
# Criterio 12 · el ejercicio nunca toca producción
# --------------------------------------------------------------------------

def test_c12_la_restauracion_se_niega_a_tocar_la_base_de_produccion(entorno):
    res = _correr("restore.sh", entorno, "--destino", "lejovista")
    assert res.returncode != 0
    assert "coincide con la base de PRODUCCION" in res.stderr


def test_c12b_la_restauracion_exige_un_destino_explicito(entorno):
    res = _correr("restore.sh", entorno)
    assert res.returncode != 0
    assert "falta --destino" in res.stderr


# --------------------------------------------------------------------------
# Criterio 1 · el destino remoto es obligatorio
# --------------------------------------------------------------------------

def test_c1_sin_destino_remoto_el_respaldo_no_corre(entorno):
    """Un respaldo que vive en el host que protege no es un respaldo: un fallo de
    disco se lleva la base y sus copias a la vez."""
    env = {**entorno["env"], "LV_BACKUP_REMOTE_PATH": ""}
    res = subprocess.run(
        ["bash", str(SCRIPTS / "backup.sh")], env=env, capture_output=True, text=True
    )
    assert res.returncode != 0
    assert "no hay destino remoto" in res.stderr
