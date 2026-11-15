import re
import math
import time
import logging
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logs to show warnings and errors with exact timestamps.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

def crear_sesion_robusta(reintentos_http=3, backoff_factor=1.5):
    """
    Create an HTTP session that reuses connections and retries network errors
    and server load-related status codes (429, 5xx).
    """
    sesion = requests.Session()
    sesion.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })

    estrategia_retry = Retry(
        total=reintentos_http,
        backoff_factor=backoff_factor,  # Delays: 1.5s, 3s, 6s...
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False
    )

    adapter = HTTPAdapter(max_retries=estrategia_retry)
    sesion.mount("https://", adapter)
    sesion.mount("http://", adapter)
    return sesion

def hacer_peticion_segura(sesion, url, params, max_intentos_app=3, timeout=20):
    """
    Send a GET request and validate the HTTP status and JSON response.
    Retry if the server returns non-JSON content or the request times out.
    """
    for intento in range(1, max_intentos_app + 1):
        try:
            respuesta = sesion.get(url, params=params, timeout=timeout)
            respuesta.raise_for_status()
            return respuesta.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            # ValueError also covers requests.exceptions.JSONDecodeError.
            if intento == max_intentos_app:
                logging.error(f"Fallo definitivo tras {max_intentos_app} intentos en {params}: {e}")
                return None
            espera = 2 ** intento
            logging.warning(f"Intento {intento}/{max_intentos_app} fallido ({e}). Reintentando en {espera}s...")
            time.sleep(espera)
    return None

def obtener_catv_desde_url(url_web):
    """Extract the category code (e.g. catv00003539) from the browser URL."""
    match = re.search(r'(catv\d+)', url_web)
    if not match:
        raise ValueError(f"No se encontró un código 'catv' válido en la URL: {url_web}")
    return match.group(1)

def scrapear_categoria_raw(url_web, momento_captura=None, delay_segundos=0.5, sesion=None):
    catv_id = obtener_catv_desde_url(url_web)
    api_url = f"https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1/products/categories/{catv_id}"

    if momento_captura is None:
        momento_captura = datetime.now().astimezone()

    cerrar_sesion_al_final = False
    if sesion is None:
        sesion = crear_sesion_robusta()
        cerrar_sesion_al_final = True

    resultados_por_pagina = 24
    params = {
        'page': 1,
        'key': 'key_r6xzz4IAoTWcipni',
        'num_results_per_page': resultados_por_pagina,
        'pre_filter_expression': '{"name":"store_availability","value":"200"}'
    }

    fecha_hoy = momento_captura.strftime("%Y-%m-%d")
    timestamp_captura = momento_captura.isoformat(timespec="seconds")
    registros_crudos = []
    pagina_actual = 1
    total_paginas = 1
    paginas_fallidas = []

    logging.info(f"Iniciando extracción para categoría: {catv_id}")

    try:
        while pagina_actual <= total_paginas:
            params['page'] = pagina_actual
            logging.info(f"  -> Descargando página {pagina_actual} de {total_paginas}...")

            datos_json = hacer_peticion_segura(sesion, api_url, params)

            if datos_json is None:
                paginas_fallidas.append(pagina_actual)
                if pagina_actual == 1:
                    logging.error(f"No se pudo obtener la página 1 de {catv_id}. Abortando categoría.")
                    break
                pagina_actual += 1
                continue

            response_data = datos_json.get('response', {})

            if pagina_actual == 1:
                total_productos = response_data.get('total_num_results', 0)
                total_paginas = max(1, math.ceil(total_productos / resultados_por_pagina))
                logging.info(f"  Total reportado por Coto: {total_productos} productos ({total_paginas} páginas).")

            resultados = response_data.get('results', [])
            if not resultados:
                break

            for item in resultados:
                fila_raw = item.get('data', {}).copy()
                fila_raw['_fecha_captura'] = fecha_hoy
                fila_raw['_timestamp_captura'] = timestamp_captura
                fila_raw['_catv_consultado'] = catv_id
                registros_crudos.append(fila_raw)

            pagina_actual += 1
            if pagina_actual <= total_paginas:
                time.sleep(delay_segundos)
    finally:
        if cerrar_sesion_al_final:
            sesion.close()

    if paginas_fallidas:
        logging.warning(f"Categoría {catv_id} finalizada con páginas fallidas: {paginas_fallidas}")

    df_raw = pd.DataFrame(registros_crudos)

    if not df_raw.empty and 'sku_id' in df_raw.columns:
        antes = len(df_raw)
        df_raw = df_raw.drop_duplicates(subset=['sku_id'], keep='first')
        logging.info(f"Limpieza de solapamiento: {antes} -> {len(df_raw)} productos únicos.")

    return df_raw

if __name__ == "__main__":
    url_golosinas = "https://www.cotodigital.com.ar/sitios/cdigi/productos/categorias/catalogo-almac%C3%A9n-golosinas/catv00003539"

    momento_captura = datetime.now().astimezone()
    df_golosinas = scrapear_categoria_raw(url_golosinas, momento_captura, delay_segundos=0.5)

    if df_golosinas.empty:
        logging.error("No se extrajeron datos. No se generará el archivo Parquet.")
    else:
        directorio_salida = Path(__file__).resolve().parent / "raw_db"
        directorio_salida.mkdir(parents=True, exist_ok=True)
        timestamp_archivo = momento_captura.strftime("%Y%m%d_%H%M%S")
        nombre_archivo = directorio_salida / f"coto_raw_golosinas_{timestamp_archivo}.parquet"

        cols_object = df_golosinas.select_dtypes(include=["object"]).columns
        df_golosinas[cols_object] = df_golosinas[cols_object].astype("string")

        df_golosinas.to_parquet(nombre_archivo, index=False)

        logging.info(f"Archivo guardado exitosamente: {nombre_archivo}")
        logging.info(f"Columnas capturadas ({len(df_golosinas.columns)}): {list(df_golosinas.columns)}")
