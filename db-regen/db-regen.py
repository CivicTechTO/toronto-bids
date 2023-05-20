#set up based on
#https://learn.microsoft.com/en-us/python/api/overview/azure/storage-file-share-readme?view=azure-python

from datetime import datetime, timedelta
from azure.storage.fileshare import ShareServiceClient, generate_account_sas, ResourceTypes, AccountSasPermissions
from azure.storage.fileshare import ShareDirectoryClient
from azure.storage.fileshare import ShareFileClient
import json
import mysql.connector
import re
from pypdf import PdfReader
from settings import db_server, db_user, db_password, db_database

fields_to_strip_html_from = [ "CallNumber", "Commodity", "CommodityType", "Type", "ShortDescription", "ShowDatePosted", "ClosingDate" ]
urlbase = "https://torontobidsstorage.file.core.windows.net/torontobids/ariba_data/"
folderbase = "ariba_data"
read_pdfs = 1
#folders_to_skip = [ 
#"Doc3592822887","Doc3597441618","Doc3672748647","Doc3717142112","Doc3768637145","Doc3779220907","Doc3797694830","Doc3819550702","Doc3839863346","Doc3841985700","Doc3853363871","Doc3863128487","Doc3869968460","Doc3870024436","Doc3889294579","Doc3908379943","Doc3911935597","Doc3911974670","Doc3918912293","Doc3919544944","Doc3920611266" 
#]

#Connect to mysql
conf = {
        'user': db_user,
        'password': db_password,
        'host': db_server,
        'database': db_database,
        'raise_on_warnings': True
}
conn = mysql.connector.connect(**conf)
cursor = conn.cursor()

#Connect to Azure file share
connection_string = "DefaultEndpointsProtocol=https;AccountName=torontobidsstorage;AccountKey=N2p8hHYf2sUFU3kd/kqE2zXx6PlalFxFJhWBN3PViFZCwuVkEa97WTIWK8SZkBfApFGhjNoajEDC+AStLvvC3Q==;EndpointSuffix=core.windows.net"
service = ShareServiceClient.from_connection_string(conn_str=connection_string)
parent_dir = ShareDirectoryClient.from_connection_string(conn_str=connection_string, share_name="torontobids", directory_path=folderbase)
all_folders = list(parent_dir.list_directories_and_files())

for this_folder_data in all_folders:
    if (this_folder_data["name"] not in folders_to_skip): 
        print("-- Starting folder: " + this_folder_data["name"])
        foldertext = "" #this is used when we're reading PDFs
        this_folder = ShareDirectoryClient.from_connection_string(conn_str=connection_string, share_name="torontobids", directory_path=folderbase+"/"+this_folder_data["name"])
        this_folder_files_list = list(this_folder.list_directories_and_files())
        attachments = {} #reset the dict of attachments, since we're at the top of a new folder
        for this_file in this_folder_files_list:
            if "json" in this_file["name"]:
                #print(this_folder_data["name"]+"/"+this_file['name'])

                file_client = ShareFileClient.from_connection_string(conn_str=connection_string, share_name="torontobids", file_path=folderbase+"/"+this_folder_data["name"]+"/"+this_file['name'])
                filecontents = file_client.download_file()

                y = json.loads(filecontents.readall())

                #print("pre:\n")
                #print(y)

                y['ClosingDate'] = y['$4']
                del y['$4']
                del y['$8']
                del y['$12']
                del y['AllAttachments']

                for key,val in y.items():
                    if not val:
                        y[key] = " "
                    elif key in fields_to_strip_html_from:
                        if val.find('<') > 0 or val.find('[') > 0:
                            y[key] = re.sub('<[^<]+?>','',val)
                            y[key] = y[key].replace('[','')
                            y[key] = y[key].replace(']','')
                    if key == 'ClosingDate' or key == 'ShowDatePosted':
                        y[key] = y[key].replace('noon','')
                        y[key] = y[key].replace('12:00','')

                #print("post:\n")
                #print(y)
                #sqlstatement = ( "INSERT INTO calls (Commodity,CommodityType,CallNumber,Type,ShortDescription,Description,ShowDatePosted,ClosingDate,SiteMeeting,ShowBuyerNameList,BuyerPhoneShow,BuyerEmailShow,Division,BuyerLocationShow,parsedtext,uuid) " + 
                #"VALUES('" + y['Commodity'] + "','" + y['CommodityType'] + "','" + y['CallNumber'] + "','" + y['Type'] + "','" + y['ShortDescription'] + "','" + y['Description'] + "',STR_TO_DATE('" + y['ShowDatePosted'] + "','%M %d, %Y'),STR_TO_DATE('" + y['ClosingDate'] + "','%M %d, %Y'),'" + y['SiteMeeting'] + "','" + 
                #y['ShowBuyerNameList'] + "','" + y['BuyerPhoneShow'] + "','" + y['BuyerEmailShow'] + "','" + y['Division'] + "','" + y['BuyerLocationShow'] + "','" + foldertext + "',UUID())" +
                #" ON DUPLICATE KEY UPDATE Commodity = '" + y['Commodity'] + "',CommodityType = '" + 
                #y['CommodityType'] + "',Type = '" + y['Type'] + "',ShortDescription = '" + y['ShortDescription'] + "',Description = '" + y['Description'] + "',ShowDatePosted = STR_TO_DATE('" + y['ShowDatePosted'] + "','%M %d, %Y'),ClosingDate = STR_TO_DATE('" + y['ClosingDate'] + "','%M %d, %Y'),SiteMeeting = '" + 
                #y['SiteMeeting'] + "',ShowBuyerNameList = '" + y['ShowBuyerNameList'] + "',BuyerPhoneShow = '" + y['BuyerPhoneShow'] + "',BuyerEmailShow = '" + y['BuyerEmailShow'] + "',Division = '" + y['Division'] + "',BuyerLocationShow = '" + y['BuyerLocationShow'] + "',parsedtext = '" + foldertext + "';")
                #print(sqlstatement)

                #SQL INSERT
                sqlreal = ( "INSERT INTO calls (Commodity,CommodityType,CallNumber,Type,ShortDescription,Description,ShowDatePosted,ClosingDate,SiteMeeting,ShowBuyerNameList,BuyerPhoneShow,BuyerEmailShow,Division,BuyerLocationShow,parsedtext,uuid) " + 
                "VALUES(%s,%s,%s,%s,%s,%s,STR_TO_DATE(%s,'%M %d, %Y'),STR_TO_DATE(%s,'%M %d, %Y'),%s,%s,%s,%s,%s,%s,%s,UUID())" +
                " ON DUPLICATE KEY UPDATE Commodity = %s,CommodityType = %s,Type = %s,ShortDescription = %s,Description = %s,ShowDatePosted = STR_TO_DATE(%s,'%M %d, %Y'),ClosingDate = STR_TO_DATE(%s,'%M %d, %Y'),SiteMeeting = " + 
                "%s,ShowBuyerNameList = %s,BuyerPhoneShow = %s,BuyerEmailShow = %s,Division = %s,BuyerLocationShow = %s,parsedtext = %s;")
                sqlval = ( y['Commodity'], y['CommodityType'],y['CallNumber'], y['Type'], y['ShortDescription'], y['Description'], y['ShowDatePosted'], y['ClosingDate'], y['SiteMeeting'], y['ShowBuyerNameList'], y['BuyerPhoneShow'], y['BuyerEmailShow'], y['Division'] , y['BuyerLocationShow'], foldertext, y['Commodity'], y['CommodityType'] , y['Type'] , y['ShortDescription'] , y['Description'] , y['ShowDatePosted'] , y['ClosingDate'], y['SiteMeeting'] , y['ShowBuyerNameList'] , y['BuyerPhoneShow'] , y['BuyerEmailShow'] , y['Division'] , y['BuyerLocationShow'], foldertext )
                cursor.execute(sqlreal,sqlval)
                conn.commit()

            else:
                #print(urlbase+"/"+this_folder_data['name']+"/"+this_file['name'])

                text = "" # this is only populated if we're reading PDFs
                if read_pdfs:
                    if this_file['name'][-4:] == ".pdf":
                        print('***PDF: '+this_file['name'])
                        
                        pdf_file_client = ShareFileClient.from_connection_string(conn_str=connection_string, share_name="torontobids", file_path=folderbase+"/"+this_folder_data["name"]+"/"+this_file['name'])
                        with open("temp.pdf", "wb") as pdf_file_handle:
                            pdf_data = pdf_file_client.download_file()
                            pdf_data.readinto(pdf_file_handle)

                        reader = PdfReader("temp.pdf")
                        number_of_pages = len(reader.pages)
                        page_range = range(number_of_pages)
                        for this_page in page_range:
                            page = reader.pages[this_page]
                            text += page.extract_text()
                            foldertext += page.extract_text()
                            #print(text)
                        print("**** " + str(len(text)) +" bytes")

                attachments[this_file['name']] = text
        
        for this_attachment_filename,this_attachment_text in attachments.items():
            #sqlstatement_attachments = ( "INSERT INTO attachments (CallNumber,filename,parsedtext,uuid) VALUES('"+this_folder_data["name"]+"','"+ this_attachment_filename + this_attachment_text +
            #"',UUID()) ON DUPLICATE KEY UPDATE CallNumber = '" + this_folder_data["name"] + "', filename = '" + this_attachment_filename + "', parsedtext = '" + this_attachment_text + "' ;" )
            #print(sqlstatement_attachments)
        
            #SQL INSERT
            #for some reason this version isn't working:
                #sqlreal = ( "INSERT INTO attachments (CallNumber,filename,parsedtext,uuid) VALUES(%s,%s,%s,UUID()) ON DUPLICATE KEY UPDATE CallNumber = %s,filename = %s,parsedtext = %s)" )
                #sqlval = ( this_folder_data["name"], this_attachment_filename, this_attachment_text, this_folder_data["name"], this_attachment_filename, this_attachment_text )
            sqlreal = ( "INSERT INTO attachments (CallNumber,filename,parsedtext,uuid) VALUES(%s,%s,%s,UUID())" )
            sqlval = ( this_folder_data["name"], this_attachment_filename, this_attachment_text )
            cursor.execute(sqlreal,sqlval)
            conn.commit()

        print("-- Ending folder: " + this_folder_data["name"])
        


