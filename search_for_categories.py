from coto_api_scraper import create_robust_session, make_safe_request


def get_level_one_categories(session, categories_url):
    """
    Download Coto's category tree and return its 118 main subcategories
    (Level 1), excluding the "Ofertas" section and deduplicating IDs.
    """
    data = make_safe_request(session, categories_url, params={})
    if not data:
        raise RuntimeError("Could not download Coto's category tree.")

    categories = []
    seen_ids = set()

    for block in data.get("output", []):
        top = block.get("topLevelCategory", {})
        top_id = str(top.get("categoryId", ""))
        top_name = top.get("displayName", "")

        # Ignore "Ofertas" because it is a filter rather than a physical department.
        if not top_id.startswith("catv"):
            continue

        for subcategory in top.get("subCategories", []):
            category_id = str(subcategory.get("categoryId", ""))
            if category_id.startswith("catv") and category_id not in seen_ids:
                seen_ids.add(category_id)
                categories.append({
                    "department": top_name,
                    "subcategory": subcategory.get("displayName", ""),
                    "category_id": category_id,
                    "web_url": f"https://www.cotodigital.com.ar/sitios/cdigi/{subcategory.get('navigationState', '')}"
                })

    return categories


categories_url = "https://www.coto.com.ar/rest/model/atg/actors/cBackOfficeActor/constructorCategories?pushSite=CotoDigital&_dynSessConf=-4248237959111523937"


def main():
    session = create_robust_session()
    try:
        categories = get_level_one_categories(session, categories_url)
    finally:
        session.close()
    print(categories)


if __name__ == "__main__":
    main()