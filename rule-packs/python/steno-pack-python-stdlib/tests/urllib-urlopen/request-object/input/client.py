from urllib.request import Request, urlopen


def fetch():
    with urlopen(Request("https://api.example.com/v1/items", headers={"accept": "text/plain"})) as r:
        return r.read()
