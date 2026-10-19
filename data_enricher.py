# data_enricher.py

# Diccionario de alias: (dpto_normalizado, mpio_coloquial_normalizado) -> MPIO_OFICIAL_DANE
ALIAS_MUNICIPIOS = {
    ("NARINO", "TUMACO"): "SAN ANDRES DE TUMACO",
    ("BOLIVAR", "CARTAGENA"): "CARTAGENA DE INDIAS",
    ("NORTEDESANTANDER", "CUCUTA"): "SAN JOSE DE CUCUTA",
    ("TOLIMA", "ARMERO"): "ARMERO GUAYABAL",
    ("CAQUETA", "SANVICENTE"): "SAN VICENTE DEL CAGUAN",
    ("ANTIOQUIA", "SANTAFEDEANTIOQUIA"): "SANTA FE DE ANTIOQUIA",
    ("ANTIOQUIA", "CARMENDEVIBORAL"): "EL CARMEN DE VIBORAL",
    ("SUCRE", "TOLU"): "SANTIAGO DE TOLU",
    ("VALLEDELCAUCA", "CALI"): "SANTIAGO DE CALI",
}

import os
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Set
import pandas as pd

# ---------------------------------------------------------
# CACHÉS EN MEMORIA (para lectura ultra-rápida O(1))
# ---------------------------------------------------------
_DANE_LOOKUP: Dict[str, Dict[str, Any]] = {}
_VARIABLES_LOOKUP: Dict[str, str] = {}
_ACCIONES_TRADICIONALES_LOOKUP: Dict[str, int] = {}
_PDET_SET: Set[int] = set()
_PNIS_SET: Set[int] = set()


def normalizar_llave(texto: Any) -> str:
    """Normaliza cadenas eliminando tildes, caracteres especiales y espacios."""
    if texto is None:
        return ""
    texto_str = str(texto).strip()
    norm = unicodedata.normalize('NFKD', texto_str).encode('ASCII', 'ignore').decode('utf-8')
    return "".join(c for c in norm.upper() if c.isalnum())


# ---------------------------------------------------------
# CARGADORES DE CATÁLOGOS LOCALES
# ---------------------------------------------------------
def get_dane_lookup(csv_path: str = "dane_mpio.csv") -> Dict[str, Dict[str, Any]]:
    """Carga y cachea dane_mpio.csv guardando códigos y nombres oficiales."""
    global _DANE_LOOKUP
    if _DANE_LOOKUP:
        return _DANE_LOOKUP

    if not os.path.exists(csv_path):
        return {}

    try:
        df = pd.read_csv(csv_path, dtype=str)
        col_dpto = next(c for c in df.columns if "departamento" in c.lower() and "c" in c.lower())
        col_mpio = next(c for c in df.columns if "municipio" in c.lower() and "c" in c.lower())
        col_conca = next(c for c in df.columns if "conca" in c.lower())
        col_nom_dpto = next((c for c in df.columns if "nombre" in c.lower() and "dep" in c.lower()), None)
        col_nom_mpio = next((c for c in df.columns if "nombre" in c.lower() and "mun" in c.lower()), None)

        lookup = {}
        for _, row in df.iterrows():
            k = normalizar_llave(row[col_conca])
            
            raw_dpto = str(row[col_dpto]).strip()
            raw_mpio = str(row[col_mpio]).strip()

            id_dpto_str = raw_dpto.zfill(2)
            id_mpio_str = raw_mpio.zfill(5)

            try:
                dane2_val = int(raw_mpio)
            except ValueError:
                dane2_val = int(id_mpio_str) if id_mpio_str.isdigit() else 0

            nom_dpto = str(row[col_nom_dpto]).strip().upper() if col_nom_dpto else ""
            nom_mpio = str(row[col_nom_mpio]).strip().upper() if col_nom_mpio else ""

            lookup[k] = {
                "id_dpto": id_dpto_str,
                "id_mpio": id_mpio_str,
                "dane2": dane2_val,
                "nombre_dpto": nom_dpto,
                "nombre_mpio": nom_mpio,
                "conca_oficial": str(row[col_conca]).strip().upper()
            }
        _DANE_LOOKUP = lookup
    except Exception as e:
        print(f"Error al cargar {csv_path}: {e}")

    return _DANE_LOOKUP

def resolver_geografia_dane(dpto_raw: str, mpio_raw: str) -> Dict[str, Any]:
    """
    Resuelve el municipio contra el DANE en cascada:
    1. Coincidencia exacta.
    2. Diccionario de alias conocidos.
    3. Contención de subcadena dentro del mismo departamento.
    """
    lookup = get_dane_lookup()
    dpto_norm = normalizar_llave(dpto_raw)
    mpio_norm = normalizar_llave(mpio_raw)

    # Nivel 1: Match directo
    key_directa = f"{dpto_norm}{mpio_norm}"
    if key_directa in lookup:
        return lookup[key_directa]

    # Nivel 2: Match por alias conocido
    mpio_oficial_alias = ALIAS_MUNICIPIOS.get((dpto_norm, mpio_norm))
    if mpio_oficial_alias:
        key_alias = f"{dpto_norm}{normalizar_llave(mpio_oficial_alias)}"
        if key_alias in lookup:
            return lookup[key_alias]

    # Nivel 3: Búsqueda por contención dentro del mismo departamento
    # (ej. si 'TUMACO' está contenido en 'SAN ANDRES DE TUMACO')
    for k, info in lookup.items():
        if k.startswith(dpto_norm):
            mpio_registrado_norm = k[len(dpto_norm):]
            if mpio_norm in mpio_registrado_norm or mpio_registrado_norm in mpio_norm:
                return info

    # Fallback si no hubo coincidencia alguna
    return {
        "id_dpto": "00",
        "id_mpio": "00000",
        "dane2": 0,
        "nombre_dpto": str(dpto_raw).strip().upper(),
        "nombre_mpio": str(mpio_raw).strip().upper(),
        "conca_oficial": f"{str(dpto_raw).strip().upper()}{str(mpio_raw).strip().upper()}"
    }

def get_variables_lookup(csv_path: str = "Variables.csv") -> Dict[str, str]:
    """Carga Variables.csv indexado por tipo_accion normalizado."""
    global _VARIABLES_LOOKUP
    if _VARIABLES_LOOKUP:
        return _VARIABLES_LOOKUP

    if not os.path.exists(csv_path):
        return {}

    try:
        df = pd.read_csv(csv_path)
        col_tipo = next(c for c in df.columns if "tipo" in c.lower() or "accion" in c.lower())
        col_acc = next(c for c in df.columns if c != col_tipo)

        lookup = {}
        for _, row in df.iterrows():
            k = normalizar_llave(row[col_tipo])
            lookup[k] = str(row[col_acc]).strip()
        _VARIABLES_LOOKUP = lookup
    except Exception as e:
        print(f"Error al cargar {csv_path}: {e}")

    return _VARIABLES_LOOKUP


def get_acciones_tradicionales_lookup(csv_path: str = "Acciones tradicionales.csv") -> Dict[str, int]:
    """Carga Acciones tradicionales.csv indexado por tipo_accion. Masacre queda fijada en 0."""
    global _ACCIONES_TRADICIONALES_LOOKUP
    if _ACCIONES_TRADICIONALES_LOOKUP:
        return _ACCIONES_TRADICIONALES_LOOKUP

    if not os.path.exists(csv_path):
        return {}

    try:
        df = pd.read_csv(csv_path)
        col_acc = next(c for c in df.columns if "accion" in c.lower() or "tradicional" in c.lower())
        col_entra = next(c for c in df.columns if "entra" in c.lower())

        lookup = {}
        for _, row in df.iterrows():
            k = normalizar_llave(row[col_acc])
            try:
                val = int(row[col_entra])
            except Exception:
                val = 0
            lookup[k] = val

        # Regla de negocio acordada: Masacre siempre es 0
        lookup[normalizar_llave("Masacre")] = 0
        _ACCIONES_TRADICIONALES_LOOKUP = lookup
    except Exception as e:
        print(f"Error al cargar {csv_path}: {e}")

    return _ACCIONES_TRADICIONALES_LOOKUP


def get_pdet_set(csv_path: str = "PDET.csv") -> Set[int]:
    """Carga los 170 códigos dane2 de municipios PDET en memoria."""
    global _PDET_SET
    if _PDET_SET:
        return _PDET_SET
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            col = next((c for c in df.columns if "pdet" in c.lower()), df.columns[0])
            _PDET_SET = set(pd.to_numeric(df[col], errors="coerce").dropna().astype(int))
        except Exception as e:
            print(f"Error al cargar {csv_path}: {e}")
    return _PDET_SET


def get_pnis_set(csv_path: str = "PNIS.csv") -> Set[int]:
    """Carga los 56 códigos dane2 de municipios PNIS en memoria."""
    global _PNIS_SET
    if _PNIS_SET:
        return _PNIS_SET
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            col = next((c for c in df.columns if "pnis" in c.lower()), df.columns[0])
            _PNIS_SET = set(pd.to_numeric(df[col], errors="coerce").dropna().astype(int))
        except Exception as e:
            print(f"Error al cargar {csv_path}: {e}")
    return _PNIS_SET


# ---------------------------------------------------------
# PARSERS AUXILIARES DE FECHAS
# ---------------------------------------------------------
def _parse_fecha(fecha_str: Any) -> datetime:
    """Intenta parsear formatos habituales de fecha (YYYY-MM-DD, D/M/YYYY, etc.)."""
    if not fecha_str or str(fecha_str).strip().lower() in ["sin información", "sin informacion", "nan", "none"]:
        return None
    formatos = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"]
    s = str(fecha_str).strip()
    for fmt in formatos:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _clean_int(val: Any) -> int:
    """Convierte de forma segura valores a entero."""
    try:
        if pd.isna(val) or val is None or str(val).strip().lower() in ["sin información", "sin informacion", ""]:
            return 0
        return int(float(val))
    except Exception:
        return 0


# ---------------------------------------------------------
# FUNCIÓN CENTRAL DE ENRIQUECIMIENTO (37 COLUMNAS)
# ---------------------------------------------------------
def enrich_row(item: Dict[str, Any], datos_noticia: Dict[str, Any]) -> Dict[str, Any]:
    """Toma un ítem validado de Streamlit + metadatos y deriva las 37 columnas exactas."""
    # 1. Geografía con resolución inteligente (maneja Tumaco -> San Andrés de Tumaco, etc.)
    dpto_input = str(item.get("Departamento", "")).strip()
    mpio_input = str(item.get("Municipio", "")).strip()

    geo_info = resolver_geografia_dane(dpto_input, mpio_input)

    dpto_final = geo_info.get("nombre_dpto") or dpto_input.upper()
    mpio_final = geo_info.get("nombre_mpio") or mpio_input.upper()
    conca_final = geo_info.get("conca_oficial") or f"{dpto_final}{mpio_final}"
    
    id_dpto = geo_info["id_dpto"]
    id_mpio = geo_info["id_mpio"]
    dane2 = geo_info["dane2"]

    # 2. Desglose de fechas
    dt_hecho = _parse_fecha(item.get("Fecha del hecho"))
    if dt_hecho:
        fecha_hecho_fmt = f"{dt_hecho.month}/{dt_hecho.day}/{dt_hecho.year}"
        year = dt_hecho.year
        month = dt_hecho.month
        day = dt_hecho.day
        semana = dt_hecho.isocalendar()[1]
    else:
        fecha_hecho_fmt = item.get("Fecha del hecho", "sin información")
        year = ""
        month = ""
        day = ""
        semana = ""

    dt_pub = _parse_fecha(datos_noticia.get("Fecha de publicación"))
    if dt_pub:
        fecha_pub_fmt = f"{dt_pub.month}/{dt_pub.day}/{dt_pub.year}"
    else:
        fecha_pub_fmt = datos_noticia.get("Fecha de publicación", "sin información")

    # 3. Categorías de acción
    tipo_accion_val = str(item.get("Tipo de acción", "")).strip()
    norm_accion = normalizar_llave(tipo_accion_val)

    accion_cat = get_variables_lookup().get(norm_accion, "N/A")
    accion_trad = get_acciones_tradicionales_lookup().get(norm_accion, 0)

    # 4. Cruces PDET y PNIS
    pdet_flag = 1 if dane2 in get_pdet_set() else 0
    pnis_flag = 1 if dane2 in get_pnis_set() else 0

    # 5. Mapeo a las 37 columnas
    return {
        "CONCA": conca_final,
        "_id": "test_id",
        "marca_temporal": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evento": item.get("Evento", ""),
        "link": datos_noticia.get("URL", "sin información"),
        "tipo_fuente": datos_noticia.get("Tipo fuente", "Otro tipo de fuente"),
        "fuente": datos_noticia.get("Fuente", "sin información"),
        "fecha_hecho": fecha_hecho_fmt,
        "fecha_publicacion": fecha_pub_fmt,
        "Semana": semana,
        "year": year,
        "month": month,
        "day": day,
        "departamento": dpto_final,
        "id_dpto": id_dpto,
        "municipio": mpio_final,
        "id_mpio": id_mpio,
        "dane2": dane2,
        "vereda": "['00000000-sin información']",
        "corregimiento": item.get("Corregimiento/Vereda", ""),
        "zona": item.get("Zona", "Rural"),
        "descripcion_hecho": item.get("Descripción del hecho", ""),
        "tipo_accion": tipo_accion_val,
        "responsable": item.get("Responsable", ""),
        "subgrupo-responsable": item.get("Sub grupo responsable", ""),
        "num_victm_civiles_heridas": _clean_int(item.get("Número de víctimas civiles heridas")),
        "num_victm_civiles_muertas": _clean_int(item.get("Número de víctimas civiles muertas")),
        "num_victm_fzapub_heridas": _clean_int(item.get("Número de víctimas de la Fuerza Pública heridas")),
        "num_victm_fzapub_muertas": _clean_int(item.get("Número de víctimas de la Fuerza Pública muertas")),
        "num_integrantes_gai_heridos": _clean_int(item.get("Número de integrantes del GAI heridos")),
        "num_integrantes_gai_muertos": _clean_int(item.get("Número de integrantes del GAI muertos")),
        "otros_number": _clean_int(item.get("Otros")),
        "tipo_revision_prensa": "Revisión de prensa FIP",
        "PDET": pdet_flag,
        "PNIS": pnis_flag,
        "Acciones tradicionales": accion_trad,
        "Accion": accion_cat,
    }


def enrich_items_for_spreadsheet(items: List[Dict[str, Any]], datos_noticia: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Enriquece una lista completa de ítems para inserción en Google Sheets."""
    return [enrich_row(item, datos_noticia) for item in items]