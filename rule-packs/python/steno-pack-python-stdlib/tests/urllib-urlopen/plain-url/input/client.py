import urllib.request


def fetch():
    return urllib.request.urlopen("https://api.example.com/v1/items").read()
