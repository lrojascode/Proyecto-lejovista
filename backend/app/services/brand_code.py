import re
from app.models.ad_account import AdAccount


def derive_brand_code(acc: AdAccount) -> str:
    """Deriva el código de marca para la nomenclatura `MARCA_TIPO_PRODUCTO_ZONA_MESAA`
    del playbook (§8, ej. `ALT_MSJ_PLATE46_JUL26`) a partir del nombre real de la
    cuenta conectada (`AdAccount.name`, ej. "Althea Pack", "Expert Farma"), en vez
    de un literal hardcodeado para cualquier tenant.

    Vive en su propio módulo (en vez de `app/workers/creation_tasks.py`) para que
    tanto la Saga de creación como el planner de IA ([planner.py](../services/ai/planner.py))
    puedan importarlo sin crear una dependencia circular entre `app.workers.tasks`
    y `app.services.ai.engine`.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", acc.name or "")
    return (cleaned[:10] or "MARCA").upper()
