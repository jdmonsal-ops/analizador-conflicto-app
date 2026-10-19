# app2.py
import os
import re
import json
import time
import streamlit as st
import pandas as pd
from difflib import get_close_matches
import trafilatura

from google import genai
from google.genai import errors

from prompt_template import SYSTEM_PROMPT
from sheets_client import append_items_to_spreadsheet

# Configuración inicial de la página
st.set_page_config(
    page_title="Monitor de Conflicto y DIH",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =========================================================
# 1. CATÁLOGOS CERRADOS OFICIALES
# =========================================================
CATALOGO_TIPO_FUENTE = ["Nacional", "Regional", "Otro tipo de fuente"]

CATALOGO_EVENTOS = [
    "Acciones de los grupos armados al margen de la ley",
    "Acciones de la Fuerza Pública y otros servidores públicos",
    "Presencia de grupos armados al margen de la ley"
]

CATALOGO_RESPONSABLES = [
    "FARC", "ELN", "EPL", "AUC", "Otro grupo armado ilegal",
    "Guerrilla no identificada", "Grupo armado ilegal no identificado",
    "Ejercito Nacional", "Policía Nacional", "Fuerza Aérea",
    "Fuerza pública no identificada", "Otro funcionario público",
    "Delincuencia común", "Desconocido", "Disidencias de las FARC"
]

CATALOGO_ACCIONES_GAI = [
    "Activación de artefacto explosivo", "Amenaza", "Ataque a bienes civiles",
    "Ataque a bienes comerciales", "Ataque a bienes culturales", "Ataque a bienes estatales",
    "Ataque a bienes indispensables para la población", "Ataque a bienes públicos",
    "Ataque a infraestructura energética", "Ataque a infraestructura de telecomunicaciones",
    "Ataque a infraestructura minera", "Ataque a infraestructura petrolera",
    "Ataque a infraestructura vial", "Ataque a instalaciones de la Fuerza Pública",
    "Ataque a instalaciones sanitarias", "Ataque a instituciones educativas",
    "Ataque a misión médica", "Ataque a personal militar/Ataque francotirador",
    "Ataque a población", "Ataques contra Aeronaves", "Bloqueo de vías",
    "Confinamiento", "Contrabando", "Desaparición forzada", "Desplazamiento forzado",
    "Duda", "Emboscada", "Enfrentamiento", "Escudo humano", "Explotación de madera ilegal",
    "Extorsión", "Homicidio", "Hostigamiento", "Hurto",
    "Incidentes por Mina Antipersonal (MAP) y Munición Sin Explotar (MUSE)",
    "Liberación", "Masacre", "Microtráfico", "Minería ilegal", "Narcotráfico",
    "Otros Eventos de Terrorismo", "Otros ataques indiscriminados", "Paro Armado",
    "Propaganda alusiva a GAI / Panfletos", "Quema de Vehículo",
    "Reclutamiento y/o utilización de menores", "Reincidencia", "Restricción a la movilidad",
    "Retén Ilegal", "Secuestro", "Tentativa de Homicidio", "Tortura",
    "Tráfico de armas", "Trata de personas", "Violencia sexual"
]

CATALOGO_ACCIONES_FZAPUB = [
    "Bombardeo", "Captura", "Combate", "Desactivación de Artefactos explosivos",
    "Desmantelamiento de Campamentos", "Desmovilización/Entrega Voluntaria",
    "Destrucción de Cultivos Ilícitos", "Destrucción de Laboratorios",
    "Destrucción de Otra Infraestructura Ilegal", "Desvinculación de menores de edad",
    "Detención arbitraria", "Fuego Amigo", "Incautación",
    "Rescate/Liberación de Secuestrados", "Vinculación con estructuras de crimen organizado"
]

CATALOGO_ZONA = ["Rural", "Urbana", "Sin información"]

@st.cache_data
def cargar_catalogo_geografico(csv_path: str = "dane_mpio.csv"):
    if not os.path.exists(csv_path):
        dptos = ["ANTIOQUIA", "ARAUCA", "CAUCA", "NARIÑO", "VALLE DEL CAUCA"]
        mpios = {
            "NARIÑO": ["SAN ANDRÉS DE TUMACO", "PASTO", "IPIALES"],
            "ANTIOQUIA": ["TOLEDO", "MEDELLÍN", "ITUANGO"],
            "CAUCA": ["TORIBÍO", "POPAYÁN", "CALDONO"],
            "ARAUCA": ["ARAUCA", "SARAVENA", "TAME"],
            "VALLE DEL CAUCA": ["SANTIAGO DE CALI", "BUENAVENTURA"]
        }
        return dptos, mpios

    df = pd.read_csv(csv_path, dtype=str)
    col_dpto = next(c for c in df.columns if "nombre" in c.lower() and "dep" in c.lower())
    col_mpio = next(c for c in df.columns if "nombre" in c.lower() and "mun" in c.lower())

    df[col_dpto] = df[col_dpto].str.strip().str.upper()
    df[col_mpio] = df[col_mpio].str.strip().str.upper()

    dptos = sorted(df[col_dpto].dropna().unique().tolist())
    mpios = {d: sorted(df[df[col_dpto] == d][col_mpio].dropna().unique().tolist()) for d in dptos}
    return dptos, mpios

LISTA_DEPARTAMENTOS, MAPA_MUNICIPIOS = cargar_catalogo_geografico()

# =========================================================
# 2. FUNCIONES AUXILIARES Y CLIENTES DE IA
# =========================================================
def buscar_indice_cercano(valor: str, opciones: list) -> int:
    if not valor or not opciones:
        return 0
    val_u = valor.strip().upper()
    op_u = [o.upper() for o in opciones]
    if val_u in op_u:
        return op_u.index(val_u)
    matches = get_close_matches(val_u, op_u, n=1, cutoff=0.35)
    if matches:
        return op_u.index(matches[0])
    for i, opt in enumerate(op_u):
        if val_u in opt or opt in val_u:
            return i
    return 0

def descomponer_actores_subgrupos(resp_str: str, subg_str: str):
    actores = [a.strip() for a in str(resp_str).split(",") if a.strip()]
    subgrupos = [s.strip() for s in str(subg_str).split(",") if s.strip()]
    while len(subgrupos) < len(actores):
        subgrupos.append("No se menciona")
    if not actores:
        actores = ["Desconocido"]
        subgrupos = ["No se menciona"]
    return [{"actor": a, "subgrupo": s} for a, s in zip(actores, subgrupos)]

def aplanar_actores_subgrupos(lista_pares):
    actores_limpios = []
    subgrupos_limpios = []
    for par in lista_pares:
        act = par.get("actor", "").strip() or "Desconocido"
        sub = par.get("subgrupo", "").strip() or "No se menciona"
        actores_limpios.append(act)
        subgrupos_limpios.append(sub)
    return ", ".join(actores_limpios), ", ".join(subgrupos_limpios)

def extraer_id_spreadsheet(url_o_id: str) -> str:
    if not url_o_id:
        return ""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_o_id)
    return match.group(1) if match else url_o_id.strip()

def extract_text_from_url(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        extracted = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            no_fallback=False
        )
        return extracted.strip() if extracted else ""
    except Exception as e:
        st.error(f"Error al descargar la URL: {e}")
        return ""

def get_gemini_client():
    if "gemini_client" not in st.session_state or st.session_state.gemini_client is None:
        api_key = st.secrets.get("GEMINI_API_KEY", None)
        if not api_key:
            st.error("Por favor configura GEMINI_API_KEY en .streamlit/secrets.toml")
            st.stop()
        st.session_state.gemini_client = genai.Client(api_key=api_key.strip())
    return st.session_state.gemini_client

def parse_json_from_response(text_content: str):
    """Extrae bloques JSON válidos con o sin markdown."""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text_content, re.DOTALL)
    raw_json = match.group(1) if match else text_content
    try:
        return json.loads(raw_json.strip())
    except json.JSONDecodeError:
        return None

def send_chat_message(message_text: str, max_retries: int = 3) -> str:
    """Envía mensaje a la IA usando el motor seleccionado (Gemini u OpenAI)."""
    
    # ------------------- CASO GEMINI -------------------
    if "gemini" in modelo_seleccionado:
        client = get_gemini_client()
        if st.session_state.chat_session is None:
            st.session_state.chat_session = client.chats.create(
                model=modelo_seleccionado,
                config=dict(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.1
                )
            )
        
        for attempt in range(max_retries):
            try:
                response = st.session_state.chat_session.send_message(message_text)
                return response.text
            except errors.ServerError:
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1))
                    continue
                st.error("Servidor saturado temporalmente (503). Intenta nuevamente en unos segundos.")
                st.stop()
            except Exception as e:
                st.error(f"Error al comunicarse con Gemini: {e}")
                st.stop()

    # ------------------- CASO OPENROUTER / OPENAI -------------------
    else:
        client = get_openai_client()
        
        if "openai_messages" not in st.session_state or st.session_state.openai_messages is None:
            st.session_state.openai_messages = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
        
        st.session_state.openai_messages.append({"role": "user", "content": message_text})
        
        # Toma el modelo de los secrets o del selector lateral
        modelo_destino = st.secrets.get("OPENROUTER_MODEL", modelo_seleccionado)
        
        for attempt in range(max_retries):
            try:
                completion = client.chat.completions.create(
                    model=modelo_destino,
                    messages=st.session_state.openai_messages,
                    temperature=0.1
                )
                respuesta_texto = completion.choices[0].message.content
                st.session_state.openai_messages.append({"role": "assistant", "content": respuesta_texto})
                return respuesta_texto
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                st.error(f"Error al comunicarse con OpenRouter: {e}")
                st.stop()

from openai import OpenAI

def get_openai_client():
    if "openai_client" not in st.session_state or st.session_state.openai_client is None:
        api_key = st.secrets.get("OPENROUTER_API_KEY") or st.secrets.get("OPENAI_API_KEY", "")
        if not api_key:
            st.error("Configura OPENROUTER_API_KEY en .streamlit/secrets.toml")
            st.stop()
        
        base_url = st.secrets.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        app_name = st.secrets.get("OPENROUTER_APP_NAME", "conflict-news-pipeline")
        
        st.session_state.openai_client = OpenAI(
            api_key=api_key.strip(),
            base_url=base_url.strip(),
            default_headers={
                "X-Title": app_name,
            }
        )
    return st.session_state.openai_client

# =========================================================
# 3. BARRA LATERAL: CONFIGURACIÓN
# =========================================================
with st.sidebar:
    st.header("⚙️ Configuración")
    
    modelo_seleccionado = st.selectbox(
        "Modelo de IA:",
        ["gemini-3.8-flash", "openai/gpt-5.4-mini"],
        index=0
    )

    st.divider()
    st.subheader("📊 Hoja de Cálculo")
    
    url_o_id_input = st.text_input(
        "Enlace o ID de Google Sheets:",
        value=st.secrets.get("SPREADSHEET_ID", ""),
        help="Pega el enlace completo o el ID alfanumérico."
    )
    sheets_id = extraer_id_spreadsheet(url_o_id_input)
    if sheets_id:
        st.caption(f"ID detectado: `{sheets_id[:12]}...`")

    with st.expander("ℹ️ Permisos para Google Sheets", expanded=True):
        st.markdown(
            "Para registrar los datos, comparte tu Google Sheet con rol de **Editor** al siguiente correo:"
        )
        service_email = st.secrets.get(
            "GOOGLE_SERVICE_EMAIL",
            "observatorio-conflicto@tu-proyecto.iam.gserviceaccount.com" # Valor por defecto si no está en secrets
        )
        st.code(service_email, language="text")
        st.caption("Copia este correo y agrégalo en el botón 'Compartir' de tu hoja.")

    st.divider()
    if st.button("🔄 Reiniciar / Nueva Noticia", use_container_width=True):
        st.session_state.etapa_flujo = 1
        st.session_state.gemini_client = None
        st.session_state.chat_session = None
        st.session_state.openai_messages = None
        st.session_state.openai_client = None
        st.session_state.texto_noticia = ""
        st.session_state.url_input = ""
        st.session_state.datos_fuente = {
            "Fuente": "",
            "Tipo fuente": "Nacional",
            "Fecha de publicación": "",
            "Descripción de la noticia": ""
        }
        st.session_state.chat_fuente_historial = []
        st.session_state.hechos_lista = []
        st.session_state.hechos_aprobados_final = []
        st.session_state.modo_vista_hechos = "tabla"
        st.session_state.cola_modificacion_indices = []
        st.rerun()

# =========================================================
# 4. VARIABLES DE ESTADO
# =========================================================
if "etapa_flujo" not in st.session_state:
    st.session_state.etapa_flujo = 1

if "gemini_client" not in st.session_state:
    st.session_state.gemini_client = None

if "chat_session" not in st.session_state:
    st.session_state.chat_session = None

if "texto_noticia" not in st.session_state:
    st.session_state.texto_noticia = ""

if "url_input" not in st.session_state:
    st.session_state.url_input = ""

if "datos_fuente" not in st.session_state:
    st.session_state.datos_fuente = {
        "Fuente": "",
        "Tipo fuente": "Nacional",
        "Fecha de publicación": "",
        "Descripción de la noticia": ""
    }

if "chat_fuente_historial" not in st.session_state:
    st.session_state.chat_fuente_historial = []

if "hechos_lista" not in st.session_state:
    st.session_state.hechos_lista = []

if "hechos_aprobados_final" not in st.session_state:
    st.session_state.hechos_aprobados_final = []

if "modo_vista_hechos" not in st.session_state:
    st.session_state.modo_vista_hechos = "tabla"

if "cola_modificacion_indices" not in st.session_state:
    st.session_state.cola_modificacion_indices = []


# =========================================================
# PASO 1: FUENTE Y DESCRIPCIÓN DE LA NOTICIA
# =========================================================
if st.session_state.etapa_flujo == 1:
    st.title("Sistema de Extracción y Validación de Conflicto")
    st.markdown("#### 1. Ingesta y Datos de la Noticia")

    col_url, col_btn_fetch = st.columns([5, 1.5])
    with col_url:
        st.session_state.url_input = st.text_input(
            "URL de la noticia:",
            value=st.session_state.url_input,
            placeholder="https://..."
        )
    with col_btn_fetch:
        st.write("")
        st.write("")
        btn_extraer = st.button("🌐 Extraer texto", use_container_width=True)

    if btn_extraer and st.session_state.url_input.strip():
        with st.spinner("Descargando contenido de la noticia..."):
            txt_ext = extract_text_from_url(st.session_state.url_input.strip())
            if txt_ext and len(txt_ext) > 80:
                st.session_state.texto_noticia = txt_ext
                st.toast("Contenido web extraído exitosamente.", icon="📄")
            else:
                st.warning("No se pudo extraer texto automáticamente de la URL. Pégalo manualmente abajo.")

    with st.expander("📝 Texto completo de la noticia (ver o editar manualmente)", expanded=bool(not st.session_state.texto_noticia)):
        st.session_state.texto_noticia = st.text_area(
            "Cuerpo del artículo:",
            value=st.session_state.texto_noticia,
            height=150,
            placeholder="Pega el contenido completo si la URL no carga o tiene muro de pago."
        )

    if st.session_state.texto_noticia:
        palabras = len(st.session_state.texto_noticia.split())
        st.caption(f"Total palabras detectadas: {palabras}")

    btn_analizar_fuente = st.button("🤖 Extraer Datos de la Noticia con IA", type="primary")

    if btn_analizar_fuente:
        if not st.session_state.texto_noticia.strip():
            st.warning("Debes ingresar o extraer el texto de la noticia antes de continuar.")
        else:
            with st.spinner(f"Analizando noticia con {modelo_seleccionado}..."):
                st.session_state.chat_session = None  # Reiniciar hilo
                prompt_inicial = f"""Analiza la siguiente noticia de conflicto armado para iniciar el Paso 1:

URL FUENTE: {st.session_state.url_input if st.session_state.url_input else 'Texto ingresado directamente'}

CONTENIDO COMPLETO DE LA NOTICIA:
\"\"\"
{st.session_state.texto_noticia}
\"\"\"

Extrae los datos solicitados del Paso 1.
"""
                resp_p1 = send_chat_message(prompt_inicial)
                
                # Parsear campos de la respuesta textual del Paso 1
                fuente_match = re.search(r"-\s*\*\*FUENTE:\*\*\s*(.*)", resp_p1, re.IGNORECASE)
                tipo_match = re.search(r"-\s*\*\*TIPO FUENTE:\*\*\s*(.*)", resp_p1, re.IGNORECASE)
                fecha_match = re.search(r"-\s*\*\*FECHA DE PUBLICACIÓN:\*\*\s*(.*)", resp_p1, re.IGNORECASE)
                desc_match = re.search(r"-\s*\*\*DESCRIPCIÓN DE LA NOTICIA:\*\*\s*(.*)", resp_p1, re.IGNORECASE | re.DOTALL)

                desc_texto = desc_match.group(1).split("¿Todo está correcto")[0].strip() if desc_match else resp_p1

                st.session_state.datos_fuente = {
                    "Fuente": fuente_match.group(1).strip() if fuente_match else "Medio periodístico",
                    "Tipo fuente": tipo_match.group(1).strip() if tipo_match else "Nacional",
                    "Fecha de publicación": fecha_match.group(1).strip() if fecha_match else time.strftime("%Y-%m-%d"),
                    "Descripción de la noticia": desc_texto
                }
                st.rerun()

    # Formulario editable de Fuente y Descripción
    if st.session_state.datos_fuente["Descripción de la noticia"]:
        st.divider()
        with st.container(border=True):
            c_tit_f, c_save_f, c_app_f = st.columns([5, 2, 2.5])
            with c_tit_f:
                st.markdown("#### Datos de la Noticia")
            with c_save_f:
                btn_guardar_f = st.button("💾 Guardar cambios", key="btn_save_f", use_container_width=True)
            with c_app_f:
                btn_aprobar_f = st.button("✅ Aprobar y continuar a Hechos", type="primary", key="btn_app_f", use_container_width=True)

            f1, f2, f3 = st.columns([3, 2, 2])
            val_fuente = f1.text_input("Fuente:", value=st.session_state.datos_fuente["Fuente"])
            
            idx_tf = buscar_indice_cercano(st.session_state.datos_fuente["Tipo fuente"], CATALOGO_TIPO_FUENTE)
            val_tipo_fuente = f2.selectbox("Tipo de fuente:", CATALOGO_TIPO_FUENTE, index=idx_tf)
            val_fecha_pub = f3.text_input("Fecha de publicación (AAAA-MM-DD):", value=st.session_state.datos_fuente["Fecha de publicación"])

            val_desc_noticia = st.text_area(
                "Descripción de la noticia:",
                value=st.session_state.datos_fuente["Descripción de la noticia"],
                height=120
            )

            # Chat complementario para ajustes asistidos
            with st.expander("💬 Solicitar ajuste a la IA mediante chat"):
                for autor, msg in st.session_state.chat_fuente_historial:
                    st.chat_message(autor).write(msg)
                prompt_chat = st.chat_input("Ej: Ajusta la fecha o agrega que fue en la vereda...")
                if prompt_chat:
                    st.session_state.chat_fuente_historial.append(("user", prompt_chat))
                    with st.spinner("Ajustando datos con la IA..."):
                        resp_chat = send_chat_message(f"Ajusta los datos del Paso 1 con esta indicación: {prompt_chat}")
                        st.session_state.chat_fuente_historial.append(("assistant", resp_chat))
                        
                        desc_m = re.search(r"-\s*\*\*DESCRIPCIÓN DE LA NOTICIA:\*\*\s*(.*)", resp_chat, re.IGNORECASE | re.DOTALL)
                        if desc_m:
                            st.session_state.datos_fuente["Descripción de la noticia"] = desc_m.group(1).split("¿Todo está correcto")[0].strip()
                    st.rerun()

            if btn_guardar_f:
                st.session_state.datos_fuente["Fuente"] = val_fuente
                st.session_state.datos_fuente["Tipo fuente"] = val_tipo_fuente
                st.session_state.datos_fuente["Fecha de publicación"] = val_fecha_pub
                st.session_state.datos_fuente["Descripción de la noticia"] = val_desc_noticia
                st.toast("Cambios en la noticia guardados.", icon="💾")

            if btn_aprobar_f:
                st.session_state.datos_fuente["Fuente"] = val_fuente
                st.session_state.datos_fuente["Tipo fuente"] = val_tipo_fuente
                st.session_state.datos_fuente["Fecha de publicación"] = val_fecha_pub
                st.session_state.datos_fuente["Descripción de la noticia"] = val_desc_noticia
                
                with st.spinner("Desglosando y estructurando las acciones individuales..."):
                    # Solicitamos directamente el JSON estructurado con base en la noticia y la descripción aprobada
                    prompt_hechos = f"""La descripción validada de la noticia es:
\"\"\"
{val_desc_noticia}
\"\"\"

Con base en esta descripción y el texto completo de la noticia, genera directamente la lista completa de acciones en un bloque JSON que contenga la clave "items_spreadsheet" según el esquema del Paso 3.
Asegúrate de incluir todos los campos requeridos para cada acción individual (Evento, Fecha del hecho, Departamento, Municipio, Corregimiento/Vereda, Zona, Descripción del hecho, Tipo de acción, Responsable, Sub grupo responsable, Número de víctimas civiles heridas, Número de víctimas civiles muertas, Número de víctimas de la Fuerza Pública heridas, Número de víctimas de la Fuerza Pública muertas, Número de integrantes del GAI heridos, Número de integrantes del GAI muertos, Otros).
"""
                    resp_hechos = send_chat_message(prompt_hechos)
                    parsed_json = parse_json_from_response(resp_hechos)
                    
                    if parsed_json and "items_spreadsheet" in parsed_json:
                        raw_items = parsed_json["items_spreadsheet"]
                        # Asignamos identificadores internos para manejo de estado
                        for idx, it in enumerate(raw_items):
                            it["uid"] = f"H-{idx+1:02d}"
                            # Asegurar campos numéricos
                            for k in ["num_civiles_heridos", "num_civiles_muertos", "num_fzapub_heridos", "num_fzapub_muertos", "num_gai_heridos", "num_gai_muertos", "otros"]:
                                if k not in it:
                                    # Mapear de nombres largos si vienen así
                                    mapeo_k = {
                                        "num_civiles_heridos": "Número de víctimas civiles heridas",
                                        "num_civiles_muertos": "Número de víctimas civiles muertas",
                                        "num_fzapub_heridos": "Número de víctimas de la Fuerza Pública heridas",
                                        "num_fzapub_muertos": "Número de víctimas de la Fuerza Pública muertas",
                                        "num_gai_heridos": "Número de integrantes del GAI heridos",
                                        "num_gai_muertos": "Número de integrantes del GAI muertos",
                                        "otros": "Otros"
                                    }
                                    it[k] = int(it.get(mapeo_k[k], 0))
                        st.session_state.hechos_lista = raw_items
                        st.session_state.etapa_flujo = 2
                        st.rerun()
                    else:
                        st.error("No se pudo obtener el desglose estructurado de acciones en JSON. Intenta de nuevo.")


# =========================================================
# PASO 2: GESTOR DE ACCIONES (TRIAJE Y EDICIÓN CON CATÁLOGOS)
# =========================================================
elif st.session_state.etapa_flujo == 2:
    st.title("Sistema de Extracción y Validación de Conflicto")
    st.markdown("#### 2. Revisión y Depuración de Hechos")

    with st.expander("📌 Contexto de la Noticia Aprobada", expanded=False):
        st.write(f"**Fuente:** {st.session_state.datos_fuente['Fuente']} ({st.session_state.datos_fuente['Tipo fuente']}) | **Fecha:** {st.session_state.datos_fuente['Fecha de publicación']}")
        st.write(st.session_state.datos_fuente["Descripción de la noticia"])

    # -----------------------------------------------------
    # VISTA 1: TABLA GENERAL CON SCROLL
    # -----------------------------------------------------
    if st.session_state.modo_vista_hechos == "tabla":
        total_hechos = len(st.session_state.hechos_lista)

        if total_hechos == 0:
            st.success("🎉 Todas las acciones de esta noticia han sido revisadas y aprobadas.")
            if st.session_state.hechos_aprobados_final:
                st.markdown(f"**Total de acciones consolidadas:** `{len(st.session_state.hechos_aprobados_final)}`")
                df_resumen = pd.DataFrame(st.session_state.hechos_aprobados_final)
                st.dataframe(df_resumen, use_container_width=True, hide_index=True)

                col_sync, _ = st.columns([2.5, 3.5])
                with col_sync:
                    if st.button("🚀 Enviar filas a Google Sheets", type="primary", use_container_width=True):
                        if not sheets_id:
                            st.error("Por favor ingresa el ID del Spreadsheet en la barra lateral.")
                        else:
                            with st.spinner("Enriqueciendo datos (37 columnas) y escribiendo en Google Sheets..."):
                                try:
                                    datos_noticia_envio = dict(st.session_state.datos_fuente)
                                    datos_noticia_envio["URL"] = st.session_state.url_input

                                    # Asegurar nombres de columnas oficiales antes de enviar
                                    items_para_sheets = []
                                    for h in st.session_state.hechos_aprobados_final:
                                        item_limpio = {
                                            "Evento": h.get("Evento", ""),
                                            "Fecha del hecho": h.get("Fecha del hecho", ""),
                                            "Departamento": h.get("Departamento", ""),
                                            "Municipio": h.get("Municipio", ""),
                                            "Corregimiento/Vereda": h.get("Corregimiento/Vereda", "Sin información"),
                                            "Zona": h.get("Zona", "Rural"),
                                            "Descripción del hecho": h.get("Descripción del hecho", ""),
                                            "Tipo de acción": h.get("Tipo de acción", ""),
                                            "Responsable": h.get("Responsable", "Desconocido"),
                                            "Sub grupo responsable": h.get("Sub grupo responsable", "No se menciona"),
                                            "Número de víctimas civiles heridas": h.get("num_civiles_heridos", 0),
                                            "Número de víctimas civiles muertas": h.get("num_civiles_muertos", 0),
                                            "Número de víctimas de la Fuerza Pública heridas": h.get("num_fzapub_heridos", 0),
                                            "Número de víctimas de la Fuerza Pública muertas": h.get("num_fzapub_muertos", 0),
                                            "Número de integrantes del GAI heridos": h.get("num_gai_heridos", 0),
                                            "Número de integrantes del GAI muertos": h.get("num_gai_muertos", 0),
                                            "Otros": h.get("otros", 0)
                                        }
                                        items_para_sheets.append(item_limpio)

                                    count = append_items_to_spreadsheet(
                                        spreadsheet_id=sheets_id,
                                        raw_items=items_para_sheets,
                                        datos_noticia=datos_noticia_envio
                                    )
                                    st.success(f"¡{count} filas sincronizadas exitosamente en el Spreadsheet!")
                                    st.balloons()
                                except Exception as e:
                                    st.error(f"Error al escribir en Google Sheets: {e}")
            st.stop()

        df_triaje = pd.DataFrame([
            {
                "Fecha": h.get("Fecha del hecho", ""),
                "Departamento": h.get("Departamento", ""),
                "Municipio": h.get("Municipio", ""),
                "Corregimiento / Vereda": h.get("Corregimiento/Vereda", ""),
                "Zona": h.get("Zona", ""),
                "Evento": h.get("Evento", ""),
                "Tipo de acción": h.get("Tipo de acción", ""),
                "Responsables": h.get("Responsable", ""),
                "Subgrupos": h.get("Sub grupo responsable", ""),
                "Víctimas / Afectaciones": (
                    f"Civ: {h.get('num_civiles_muertos', 0)}M / {h.get('num_civiles_heridos', 0)}H | "
                    f"FP: {h.get('num_fzapub_muertos', 0)}M / {h.get('num_fzapub_heridos', 0)}H | "
                    f"GAI: {h.get('num_gai_muertos', 0)}M / {h.get('num_gai_heridos', 0)}H | "
                    f"Otros: {h.get('otros', 0)}"
                ),
                "Descripción": h.get("Descripción del hecho", "")
            }
            for h in st.session_state.hechos_lista
        ])

        c_info, c_aprobar, c_modificar, c_descartar = st.columns([2.5, 2.5, 2, 2])
        with c_info:
            st.markdown(f"**Pendientes por resolver:** `{total_hechos}`")
        with c_aprobar:
            btn_aprobar_sel = st.button("✅ Aprobar seleccionados", type="primary", use_container_width=True)
        with c_modificar:
            btn_modificar_sel = st.button("✏️ Modificar seleccionados", use_container_width=True)
        with c_descartar:
            btn_descartar_sel = st.button("🗑️ Descartar seleccionados", use_container_width=True)

        event = st.dataframe(
            df_triaje,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            column_config={
                "Fecha": st.column_config.TextColumn("Fecha", width=105),
                "Departamento": st.column_config.TextColumn("Departamento", width=120),
                "Municipio": st.column_config.TextColumn("Municipio", width=160),
                "Corregimiento / Vereda": st.column_config.TextColumn("Corregimiento / Vereda", width=160),
                "Zona": st.column_config.TextColumn("Zona", width=80),
                "Evento": st.column_config.TextColumn("Evento", width=220),
                "Tipo de acción": st.column_config.TextColumn("Tipo de acción", width=160),
                "Responsables": st.column_config.TextColumn("Responsables", width=200),
                "Subgrupos": st.column_config.TextColumn("Subgrupos", width=160),
                "Víctimas / Afectaciones": st.column_config.TextColumn("Víctimas (Civ | FP | GAI | Otros)", width=280),
                "Descripción": st.column_config.TextColumn("Descripción de la acción", width=380)
            }
        )

        filas_seleccionadas = list(event.selection.rows) if event.selection.rows else []

        if btn_aprobar_sel:
            if not filas_seleccionadas:
                st.warning("Selecciona al menos una fila marcando su casilla en la tabla.")
            else:
                for idx in filas_seleccionadas:
                    st.session_state.hechos_aprobados_final.append(st.session_state.hechos_lista[idx])
                for idx in sorted(filas_seleccionadas, reverse=True):
                    st.session_state.hechos_lista.pop(idx)
                st.toast(f"{len(filas_seleccionadas)} acción(es) aprobada(s).", icon="✅")
                st.rerun()

        if btn_modificar_sel:
            if not filas_seleccionadas:
                st.warning("Selecciona al menos una fila marcando su casilla en la tabla.")
            else:
                st.session_state.cola_modificacion_indices = list(filas_seleccionadas)
                st.session_state.modo_vista_hechos = "edicion_tarjeta"
                st.rerun()

        if btn_descartar_sel:
            if not filas_seleccionadas:
                st.warning("Selecciona al menos una fila marcando su casilla en la tabla.")
            else:
                for idx in sorted(filas_seleccionadas, reverse=True):
                    st.session_state.hechos_lista.pop(idx)
                st.toast(f"{len(filas_seleccionadas)} acción(es) descartada(s).", icon="🗑️")
                st.rerun()

    # -----------------------------------------------------
    # VISTA 2: TARJETA DE EDICIÓN CON CATÁLOGOS DANE
    # -----------------------------------------------------
    elif st.session_state.modo_vista_hechos == "edicion_tarjeta":
        if not st.session_state.cola_modificacion_indices:
            st.session_state.modo_vista_hechos = "tabla"
            st.rerun()

        current_idx = st.session_state.cola_modificacion_indices[0]
        if current_idx >= len(st.session_state.hechos_lista):
            st.session_state.cola_modificacion_indices.pop(0)
            st.rerun()

        actual = st.session_state.hechos_lista[current_idx]
        pendientes_en_cola = len(st.session_state.cola_modificacion_indices)

        key_pares_estado = f"actores_pares_{current_idx}"
        if key_pares_estado not in st.session_state:
            st.session_state[key_pares_estado] = descomponer_actores_subgrupos(
                actual.get("Responsable", "Desconocido"),
                actual.get("Sub grupo responsable", "No se menciona")
            )

        with st.container(border=True):
            c_tit, c_volver, c_save, c_app = st.columns([4, 2, 2, 2])
            with c_tit:
                st.markdown(f"#### ✏️ Editando: **{actual.get('Municipio', '')}** — `{actual.get('Tipo de acción', '')}` `({pendientes_en_cola} pendiente(s))`")
            with c_volver:
                if st.button("↩️ Volver a la tabla", use_container_width=True):
                    if key_pares_estado in st.session_state:
                        del st.session_state[key_pares_estado]
                    st.session_state.modo_vista_hechos = "tabla"
                    st.rerun()
            with c_save:
                btn_guardar_h = st.button("💾 Guardar cambios", key=f"sh_{current_idx}", use_container_width=True)
            with c_app:
                btn_aprobar_h = st.button("✅ Guardar y siguiente", type="primary", key=f"ah_{current_idx}", use_container_width=True)

            st.divider()

            # Fila 1: Geografía y Fecha (Municipios filtrados por Departamento)
            g1, g2, g3, g4, g5 = st.columns([2, 2, 2, 1.5, 1.5])
            idx_dp = buscar_indice_cercano(actual.get("Departamento", ""), LISTA_DEPARTAMENTOS)
            f_dpto = g1.selectbox("Departamento:", LISTA_DEPARTAMENTOS, index=idx_dp, key=f"dp_{current_idx}")

            mpios_disp = MAPA_MUNICIPIOS.get(f_dpto, [actual.get("Municipio", "")])
            idx_mp = buscar_indice_cercano(actual.get("Municipio", ""), mpios_disp)
            f_mpio = g2.selectbox("Municipio:", mpios_disp, index=idx_mp, key=f"mp_{current_idx}_{f_dpto}")

            f_vereda = g3.text_input("Corregimiento / Vereda:", value=actual.get("Corregimiento/Vereda", "Sin información"), key=f"vr_{current_idx}")
            idx_zn = buscar_indice_cercano(actual.get("Zona", "Rural"), CATALOGO_ZONA)
            f_zona = g4.selectbox("Zona:", CATALOGO_ZONA, index=idx_zn, key=f"zn_{current_idx}")
            f_fecha = g5.text_input("Fecha (AAAA-MM-DD):", value=actual.get("Fecha del hecho", ""), key=f"fc_{current_idx}")

            # Fila 2: Evento y Acción dinámica
            e1, e2 = st.columns(2)
            idx_ev = buscar_indice_cercano(actual.get("Evento", ""), CATALOGO_EVENTOS)
            f_evento = e1.selectbox("Evento:", CATALOGO_EVENTOS, index=idx_ev, key=f"ev_{current_idx}")

            acciones_disp = CATALOGO_ACCIONES_FZAPUB if "Fuerza Pública" in f_evento else CATALOGO_ACCIONES_GAI
            idx_ac = buscar_indice_cercano(actual.get("Tipo de acción", ""), acciones_disp)
            f_accion = e2.selectbox("Tipo de acción:", acciones_disp, index=idx_ac, key=f"ac_{current_idx}")

            # Fila 3: Responsables y Subgrupos emparejados
            st.markdown("**Responsables:**")
            pares_actuales = st.session_state[key_pares_estado]
            nuevos_pares = []

            for p_idx, par in enumerate(pares_actuales):
                col_act, col_subg, col_del_act = st.columns([4, 4, 1])
                with col_act:
                    idx_r = buscar_indice_cercano(par["actor"], CATALOGO_RESPONSABLES)
                    sel_actor = st.selectbox(
                        f"Responsable {p_idx+1}:",
                        CATALOGO_RESPONSABLES,
                        index=idx_r,
                        key=f"act_{current_idx}_{p_idx}"
                    )
                with col_subg:
                    txt_subg = st.text_input(
                        f"Subgrupo del Responsable {p_idx+1}:",
                        value=par["subgrupo"],
                        key=f"sub_{current_idx}_{p_idx}"
                    )
                with col_del_act:
                    st.write("")
                    st.write("")
                    if st.button("🗑️", key=f"del_par_{current_idx}_{p_idx}", help="Eliminar este actor"):
                        pares_actuales.pop(p_idx)
                        st.rerun()

                nuevos_pares.append({"actor": sel_actor, "subgrupo": txt_subg})

            if st.button("➕ Agregar otro responsable"):
                pares_actuales.append({"actor": "Desconocido", "subgrupo": "No se menciona"})
                st.rerun()

            # Fila 4: Descripción
            f_desc = st.text_area("Descripción de la acción:", value=actual.get("Descripción del hecho", ""), height=70, key=f"ds_{current_idx}")

            # Fila 5: Víctimas y afectaciones
            st.caption("Afectaciones y Víctimas:")
            v1, v2, v3, v4, v5, v6, v7 = st.columns(7)
            f_ch = v1.number_input("Civiles Heridos", value=int(actual.get("num_civiles_heridos", 0)), min_value=0, step=1, key=f"ch_{current_idx}")
            f_cm = v2.number_input("Civiles Muertos", value=int(actual.get("num_civiles_muertos", 0)), min_value=0, step=1, key=f"cm_{current_idx}")
            f_fh = v3.number_input("Fza Púb Heridos", value=int(actual.get("num_fzapub_heridos", 0)), min_value=0, step=1, key=f"fh_{current_idx}")
            f_fm = v4.number_input("Fza Púb Muertos", value=int(actual.get("num_fzapub_muertos", 0)), min_value=0, step=1, key=f"fm_{current_idx}")
            f_gh = v5.number_input("GAI Heridos", value=int(actual.get("num_gai_heridos", 0)), min_value=0, step=1, key=f"gh_{current_idx}")
            f_gm = v6.number_input("GAI Muertos", value=int(actual.get("num_gai_muertos", 0)), min_value=0, step=1, key=f"gm_{current_idx}")
            f_ot = v7.number_input("Otros", value=int(actual.get("otros", 0)), min_value=0, step=1, key=f"ot_{current_idx}")

        if btn_guardar_h or btn_aprobar_h:
            resp_plano, subg_plano = aplanar_actores_subgrupos(nuevos_pares)

            actual["Departamento"] = f_dpto
            actual["Municipio"] = f_mpio
            actual["Corregimiento/Vereda"] = f_vereda
            actual["Zona"] = f_zona
            actual["Fecha del hecho"] = f_fecha
            actual["Evento"] = f_evento
            actual["Tipo de acción"] = f_accion
            actual["Responsable"] = resp_plano
            actual["Sub grupo responsable"] = subg_plano
            actual["Descripción del hecho"] = f_desc
            actual["num_civiles_heridos"] = f_ch
            actual["num_civiles_muertos"] = f_cm
            actual["num_fzapub_heridos"] = f_fh
            actual["num_fzapub_muertos"] = f_fm
            actual["num_gai_heridos"] = f_gh
            actual["num_gai_muertos"] = f_gm
            actual["otros"] = f_ot

            if btn_guardar_h:
                st.toast("Cambios guardados en memoria.", icon="💾")
                st.rerun()

            if btn_aprobar_h:
                if key_pares_estado in st.session_state:
                    del st.session_state[key_pares_estado]
                st.session_state.cola_modificacion_indices.pop(0)
                st.toast("Acción validada.", icon="✅")
                if not st.session_state.cola_modificacion_indices:
                    st.session_state.modo_vista_hechos = "tabla"
                st.rerun()