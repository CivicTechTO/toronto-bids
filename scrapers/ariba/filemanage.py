import re
from pathlib import Path
import zipfile

import pandas as pd
from bs4 import BeautifulSoup


def extract_zip_recursively(zip_file: Path, new_dir: Path):
    print(f"Extracting contents of {zip_file} to {new_dir}")
    with zipfile.ZipFile(zip_file, "r") as z:
        z.extractall(new_dir)
        for name in z.namelist():
            if name.endswith(".zip"):
                nested_zip = new_dir / name
                extract_zip_recursively(nested_zip, new_dir)
    zip_file.unlink()


def extract_zip_and_move_html(directory: Path):
    for filename in directory.iterdir():
        if filename.suffix == ".html":
            html_file = filename

            new_dir = filename.with_suffix("")
            print(f"Creating directory: {new_dir}")
            new_dir.mkdir(parents=True, exist_ok=True)

            print(f"Moving {html_file} to {new_dir / html_file.name}")
            html_file.rename(new_dir / html_file.name)

            zip_file = filename.with_suffix(".zip")

            if zip_file.exists():

                extract_zip_recursively(zip_file, new_dir)

                print(f"Removing {zip_file}")
                try:
                    zip_file.unlink()
                except FileNotFoundError:
                    pass


def move_pdfs(download_directory: Path, new_directory: Path):
    for file in download_directory.glob('*.pdf'):
        name_parts = file.stem.split(' ')
        docnum = [part for part in name_parts if 'Doc' in part]
        if len(docnum) == 1:
            rfp_path = new_directory / Path(docnum[0])
            rfp_path.mkdir(exist_ok=True)
            print(f'Moving {file.parts[-1]} to {rfp_path}')
            file.rename(rfp_path / file.parts[-1])


def parse_html(directory: Path) -> pd.DataFrame:
    results = []
    for folder in directory.iterdir():
        if folder.is_dir():
            for file in folder.iterdir():
                if file.suffix == '.html':
                    dictionary = {'ID': file.parts[1]}
                    try:
                        with open(file, 'r') as f:
                            soup = BeautifulSoup(f, parser='html5lib')
                    except Exception as e:
                        print(e)
                        continue
                    dictionary['title'] = soup.title.string
                    try:
                        content = soup.find_all('div', class_='postingHeaderContent')[0]
                    except IndexError:
                        print(file)
                        continue

                    with open('temp.html', 'w') as f:
                        f.write(str(content))
                    tables = pd.read_html('temp.html')
                    for k, v in dict(tables[0].values).items():
                        if k == k:
                            dictionary[k] = v
                    dictionary['Product Categories'] = [t.strip() for t in
                                                        re.findall('([A-Z][^A-Z]+)', tables[1].loc[0, 0])[3:]]
                    dictionary['summary'] = content.find_all('div', class_='postingHeaderNormalText postingHeaderPadding')[
                        0].text
                    results.append(dictionary)
    return pd.DataFrame(results)