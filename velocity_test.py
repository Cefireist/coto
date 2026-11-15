import time
import requests
import pandas as pd

API_URL = "https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/api/v1/products/categories/catv00003539"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

def benchmark_request_speed(delays_to_test=(0.5, 0.25, 0.1, 0.05, 0.0), requests_per_level=40):
    session = requests.Session()
    session.headers.update(HEADERS)
    summary = []

    for delay in delays_to_test:
        print(f"\n--- Testing delay = {delay}s ({requests_per_level} requests) ---")
        latencies_ms = []
        successful_requests = 0
        rejected_requests = 0
        first_error = None
        block_start = time.perf_counter()

        for request_index in range(requests_per_level):
            # Rotate between pages 1 and 30 to avoid artificially cached responses.
            page = (request_index % 30) + 1
            params = {
                'page': page,
                'key': 'key_r6xzz4IAoTWcipni',
                'num_results_per_page': 24,
                'pre_filter_expression': '{"name":"store_availability","value":"200"}'
            }

            request_start = time.perf_counter()
            try:
                response = session.get(API_URL, params=params, timeout=15)
                latency_ms = (time.perf_counter() - request_start) * 1000
                latencies_ms.append(latency_ms)

                if response.status_code == 200:
                    response_data = response.json()
                    if response_data.get('response', {}).get('results'):
                        successful_requests += 1
                    else:
                        rejected_requests += 1
                        first_error = "HTTP 200 but 'results' is empty (possible soft block)"
                        break
                else:
                    rejected_requests += 1
                    first_error = f"HTTP {response.status_code}"
                    print(f"  [!] Rejection detected on request #{request_index + 1}: {first_error}")
                    break

            except Exception as error:
                rejected_requests += 1
                first_error = str(type(error).__name__)
                print(f"  [!] Exception on request #{request_index + 1}: {first_error}")
                break

            if delay > 0:
                time.sleep(delay)

        total_duration = time.perf_counter() - block_start
        average_latency_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0
        requests_per_second = successful_requests / total_duration if total_duration > 0 else 0
        # Estimate the duration of a full scan of approximately 350 pages.
        estimated_catalog_duration = (350 / requests_per_second) if requests_per_second > 0 else None

        summary.append({
            "Delay_s": delay,
            "Successful_Requests": f"{successful_requests}/{requests_per_level}",
            "Average_Latency_ms": round(average_latency_ms, 1),
            "Requests_per_Second": round(requests_per_second, 2),
            "Estimated_350_Pages_s": round(estimated_catalog_duration, 1) if estimated_catalog_duration else "N/A",
            "Status": "OK" if rejected_requests == 0 else first_error
        })

        # Stop more aggressive tests after a rejection to avoid a prolonged IP block.
        if rejected_requests > 0:
            print("Stopping the test to avoid a prolonged IP block.")
            break

        # Cool down between test levels.
        time.sleep(3)

    session.close()
    print("\n=== BENCHMARK RESULTS ===")
    print(pd.DataFrame(summary).to_string(index=False))

if __name__ == "__main__":
    benchmark_request_speed()