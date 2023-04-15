import requests
import xml.etree.ElementTree as ET

# Converted from PHP by ChatGPT

my_xml_data = requests.get('https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/645e682e-5504-46fe-aaa4-cb27e6384381/resource/2160e5d4-6c6c-4895-95ce-9831b3553dc6/download/Construction%20Services,%20Goods%20&%20Services,%20and%20Professional%20Services.xml').content
xml = ET.fromstring(my_xml_data)

data = {}

for entryobj in xml.findall('.//viewentry'):
    for gabe in entryobj.findall('.//entrydata'):
        if gabe.attrib['name'].strip() != "$12":
            data[entryobj.attrib['position'].strip()][gabe.attrib['name'].strip()] = gabe.text.strip()
        if gabe.attrib['name'].strip() == "AllAttachments":
            urls = []
            links = gabe.findall('.//a')
            for link in links:
                urls.append(link.attrib['href'])
            if urls:
                data[entryobj.attrib['position'].strip()]['urls'] = urls

count = 0
for r in data.values():
    if not r.get('urls'):
        r['urls'] = []
    if not r.get('CallNumber'):
        r['CallNumber'] = f'blank_{count}'
    short_description = r['ShortDescription'].replace("'", "\\'")
    description = r['Description'].replace("'", "\\'")
    division = r['Division'].replace("'", "\\'")
    buyer_location_show = r['BuyerLocationShow'].replace("'", "\\'")
    print(f"INSERT INTO fromxml (Commodity,CommodityType,CallNumber,Type,ShortDescription,Description,ShowDatePosted,ClosingDate,SiteMeeting,ShowBuyerNameList,BuyerPhoneShow,BuyerEmailShow,Division,BuyerLocationShow,urls,uuid) "
          f"VALUES('{r['Commodity']}','{r['CommodityType']}','{r['CallNumber']}','{r['Type']}','{short_description}','{description}','{r['ShowDatePosted']}','{r['$4']}','{r['SiteMeeting']}','{r['ShowBuyerNameList']}','{r['BuyerPhoneShow']}','{r['BuyerEmailShow']}','{division}','{buyer_location_show}','{','.join(r['urls'])}',UUID());")
    count += 1

print(f"\n\nCount: {count}\n")
