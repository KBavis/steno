def summarize(pr: dict, data: dict):
    title = pr.get("title")
    details = data.get("detail", [])
    return title, details
