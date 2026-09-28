import aiosqlite

from media_management.roots import Root


async def delete_blockers(conn: aiosqlite.Connection, root: Root, file_row: aiosqlite.Row) -> list[str]:
    """Motivos por los que no se puede mandar el fichero a la papelera; vacío si se puede.

    En las raíces que exigen segunda copia, lo que no se sabe que esté en otro sitio
    cuenta como ausente. Hasta que exista el inventario de la copia offline (v2) no
    se sabe de ninguno, y es correcto que no se pueda borrar nada ahí."""
    reasons: list[str] = []
    if not root.deletable:
        reasons.append("esta raíz no permite borrar")
    if root.requires_second_copy:
        reasons.append("no consta una segunda copia de este fichero, y en esta raíz solo se "
                       "borra lo que la tiene: puede ser el único ejemplar")
    return reasons


def rename_allowed(root: Root) -> bool:
    return root.renamable and not root.synced
