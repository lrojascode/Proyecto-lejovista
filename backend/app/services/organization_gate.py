"""Compuerta de estado de la Organización (spec 01).

Suspender una Organización tiene que detener cinco cosas: login, API, sync, IA y
write-back. Las dos primeras las corta `get_current_user`, que es el cuello por
el que pasa toda petición autenticada. Las otras tres no tienen sesión delante
—corren en workers— así que necesitan su propia comprobación, y este módulo es
el único lugar donde vive para que no se olvide en la siguiente tarea que
alguien escriba.

Antes solo la ejecución de una AIAction miraba el estado: una Organización
suspendida seguía sincronizando cada hora, gastando llamadas a Meta y tokens de
IA, y podía escribir en las cuentas de un cliente al que ya se le había cortado
el servicio.
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization

logger = logging.getLogger("lejovista.org_gate")

# Único estado que habilita operación. `SUSPENDED` lo pone la agencia;
# `INACTIVE` lo pone la baja de una organización residual. Ninguno de los dos
# debe seguir consumiendo Meta ni IA, así que se comprueba por lo que SÍ vale.
ACTIVE_STATUS = "ACTIVE"


class OrganizationNotActive(Exception):
    """La Organización no está en condiciones de operar.

    Excepción y no un booleano porque los caminos que la levantan son escrituras
    hacia Meta: quien la ignore tiene que romperse, no continuar.
    """

    def __init__(self, org_id: str, org_status: str):
        self.org_id = org_id
        self.org_status = org_status
        super().__init__(
            f"La organización {org_id} está en estado {org_status}; "
            f"sync, IA y write-back quedan detenidos."
        )


async def is_organization_active(db: AsyncSession, org_id: Optional[str]) -> bool:
    """True si `org_id` puede operar. Sin organización, no hay nada que habilitar."""
    if not org_id:
        return False
    org = await db.get(Organization, org_id)
    return org is not None and org.status == ACTIVE_STATUS


async def assert_organization_active(db: AsyncSession, org_id: Optional[str]) -> None:
    """Levanta `OrganizationNotActive` si la Organización no puede operar."""
    if not org_id:
        raise OrganizationNotActive(str(org_id), "SIN_ORGANIZACION")
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrganizationNotActive(org_id, "NO_EXISTE")
    if org.status != ACTIVE_STATUS:
        raise OrganizationNotActive(org_id, org.status)
