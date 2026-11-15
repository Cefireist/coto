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

def create_robust_session(http_retries=3, backoff_factor=1.5):
    """
    Create an HTTP session that reuses connections and retries network errors
    and server load-related status codes (429, 5xx).
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })

    retry_strategy = Retry(
        total=http_retries,
        backoff_factor=backoff_factor,  # Delays: 1.5s, 3s, 6s...
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def make_safe_request(session, url, params, max_app_attempts=3, timeout=20):
    """
    Send a GET request and validate the HTTP status and JSON response.
    Retry if the server returns non-JSON content or the request times out.
    """
    for attempt in range(1, max_app_attempts + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as error:
            # ValueError also covers requests.exceptions.JSONDecodeError.
            if attempt == max_app_attempts:
                logging.error(
                    f"Request failed after {max_app_attempts} attempts with params {params}: {error}"
                )
                return None
            wait_seconds = 2 ** attempt
            logging.warning(
                f"Attempt {attempt}/{max_app_attempts} failed ({error}). "
                f"Retrying in {wait_seconds}s..."
            )
            time.sleep(wait_seconds)
    return None

def extract_category_id_from_url(web_url):
    """Extract the category code (e.g. catv00003539) from the browser URL."""
    match = re.search(r'(catv\d+)', web_url)
    if not match:
        raise ValueError(f"No valid 'catv' category code was found in URL: {web_url}")
    return match.group(1)

def scrape_category_raw(web_url, capture_time=None, delay_seconds=0.5, session=None):
    category_id = extract_category_id_from_url(web_url)
    api_url = f"https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1/products/categories/{category_id}"

    if capture_time is None:
        capture_time = datetime.now().astimezone()

    close_session_at_end = False
    if session is None:
        session = create_robust_session()
        close_session_at_end = True

    products_per_page = 24
    params = {
        'page': 1,
        'key': 'key_r6xzz4IAoTWcipni',
        'num_results_per_page': products_per_page,
        'pre_filter_expression': '{"name":"store_availability","value":"200"}'
    }

    capture_date = capture_time.strftime("%Y-%m-%d")
    capture_timestamp = capture_time.isoformat(timespec="seconds")
    raw_records = []
    current_page = 1
    total_pages = 1
    failed_pages = []

    logging.info(f"Starting extraction for category: {category_id}")

    try:
        while current_page <= total_pages:
            params['page'] = current_page
            logging.info(f"  -> Downloading page {current_page} of {total_pages}...")

            response_json = make_safe_request(session, api_url, params)

            if response_json is None:
                failed_pages.append(current_page)
                if current_page == 1:
                    logging.error(
                        f"Could not retrieve page 1 for category {category_id}. "
                        "Aborting category."
                    )
                    break
                current_page += 1
                continue

            response_data = response_json.get('response', {})

            if current_page == 1:
                total_products = response_data.get('total_num_results', 0)
                total_pages = max(1, math.ceil(total_products / products_per_page))
                logging.info(
                    f"  Total reported by Coto: {total_products} products "
                    f"({total_pages} pages)."
                )

            results = response_data.get('results', [])
            if not results:
                break

            for item in results:
                raw_row = item.get('data', {}).copy()
                raw_row['_capture_date'] = capture_date
                raw_row['_capture_timestamp'] = capture_timestamp
                raw_row['_queried_category_id'] = category_id
                raw_records.append(raw_row)

            current_page += 1
            if current_page <= total_pages:
                time.sleep(delay_seconds)
    finally:
        if close_session_at_end:
            session.close()

    if failed_pages:
        logging.warning(
            f"Category {category_id} finished with failed pages: {failed_pages}"
        )

    raw_df = pd.DataFrame(raw_records)

    if not raw_df.empty and 'sku_id' in raw_df.columns:
        rows_before_deduplication = len(raw_df)
        raw_df = raw_df.drop_duplicates(subset=['sku_id'], keep='first')
        logging.info(
            f"Removed overlapping products: {rows_before_deduplication} "
            f"-> {len(raw_df)} unique products."
        )

    return raw_df

if __name__ == "__main__":
    golosinas_url = "https://www.cotodigital.com.ar/sitios/cdigi/productos/categorias/catalogo-almac%C3%A9n-golosinas/catv00003539"

    capture_time = datetime.now().astimezone()
    golosinas_df = scrape_category_raw(golosinas_url, capture_time, delay_seconds=0.5)

    if golosinas_df.empty:
        logging.error("No data was extracted. The Parquet file will not be created.")
    else:
        output_directory = Path(__file__).resolve().parent / "raw_db"
        output_directory.mkdir(parents=True, exist_ok=True)
        filename_timestamp = capture_time.strftime("%Y%m%d_%H%M%S")
        output_path = output_directory / f"coto_raw_golosinas_{filename_timestamp}.parquet"

        object_columns = golosinas_df.select_dtypes(include=["object"]).columns
        golosinas_df[object_columns] = golosinas_df[object_columns].astype("string")

        golosinas_df.to_parquet(output_path, index=False)

        logging.info(f"File saved successfully: {output_path}")
        logging.info(
            f"Captured {len(golosinas_df.columns)} columns: "
            f"{list(golosinas_df.columns)}"
        )
