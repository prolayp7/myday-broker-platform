from repositories import country as repo


def list_countries():
    return repo.list_all().data
