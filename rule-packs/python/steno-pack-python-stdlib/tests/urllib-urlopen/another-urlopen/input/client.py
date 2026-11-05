from mylib.net import urlopen


def fetch():
    return urlopen("https://api.example.com/v1/items")
