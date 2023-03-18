# Scrapers

Running the main rfp scrapers make sure `username.key` and `password.key` are present with your ariba login credentials, from the root level of the project run:
```shell
# run either the make command
make run-rfp-scraper

# or using the regular python commands
source venv/bin/acitvate
python3 scrapers/ariba/rfp_scraper.py
```

Formatting the python code using black:
```shell
# using the make target
make lint-python

# using python commands
python3 -m black scrapers/
```