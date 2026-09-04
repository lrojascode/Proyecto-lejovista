"""Medios REALES para los tests de carga (spec 07).

La validacion ya no mira el tipo declarado sino el contenido, asi que un
`b"\\xff\\xd8\\xff\\xd9"` con nombre de JPEG deja de servir como archivo de
prueba — y hace bien en dejar de servir: era exactamente el caso que un
ejecutable renombrado aprovechaba.

`mini.mp4` son 2 KB generados con ffmpeg (320x320, 1 s). Se versiona en vez de
generarse en cada corrida para no depender de que ffmpeg este instalado donde
corran los tests.
"""
from pathlib import Path

_AQUI = Path(__file__).parent


def jpg_real() -> bytes:
    return (_AQUI / "mini.jpg").read_bytes()


def mp4_real() -> bytes:
    return (_AQUI / "mini.mp4").read_bytes()
