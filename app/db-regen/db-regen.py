# this script will create or update a mysql database from the files on the Azure file share
# it will also attempt to grab text from pdf files -- but not doc(x), ppt(x) or xls(x) files

#set up based on
#https://learn.microsoft.com/en-us/python/api/overview/azure/storage-file-share-readme?view=azure-python

from datetime import datetime, timedelta
from azure.storage.fileshare import ShareServiceClient, generate_account_sas, ResourceTypes, AccountSasPermissions
from azure.storage.fileshare import ShareDirectoryClient
from azure.storage.fileshare import ShareFileClient
import json
import mysql.connector
import re
import os, stat
from pypdf import PdfReader
from settings import db_server, db_user, db_password, db_database
from signal import signal, SIGPIPE, SIG_DFL  
signal(SIGPIPE,SIG_DFL) 

fields_to_strip_html_from = [ "CallNumber", "Commodity", "CommodityType", "Type", "ShortDescription", "ShowDatePosted", "ClosingDate" ]
urlbase = "https://torontobidsstorage.file.core.windows.net/torontobids/ariba_data/"
folderbase = "ariba_data"
read_pdfs = 1
folders_to_skip = [ "Doc3921046980", "Doc3948982724", "Doc3952713027", "Doc3964425639", "Doc3964997028", "Doc3968699837", "Doc3971697547", "Doc3973032085", "Doc3992828327"]
folder_to_start_with = "Doc3992828327"

def process_folder(folder_name, CallNumber, subfolder = 0):
    print("-- Starting folder: " + folder_name)
    foldertext = "" #this is used when we're reading PDFs
    this_folder = ShareDirectoryClient.from_connection_string(conn_str=connection_string, share_name="torontobids", directory_path=folderbase+"/"+folder_name)
    this_folder_files_list = list(this_folder.list_directories_and_files())
    attachments = {} #reset the dict of attachments, since we're at the top of a new folder
    jsonfiles = []

    for this_file in this_folder_files_list:
        if this_file["is_directory"]:
            foldertext += process_folder(folder_name+"/"+this_file['name'],CallNumber,1)
        elif "html" in this_file["name"]:
            pass
        elif "json" in this_file["name"] and subfolder == 0:
            jsonfiles.append(this_file["name"])
        else:
            print("parsing: " + folderbase+"/"+folder_name+"/"+this_file['name'])

            #check to see if it's already parsed -- actually, this isn't going to work, because of foldertext!
            #sqlcheck = ( "SELECT LENGTH(parsedtext) as textlength FROM attachments WHERE CallNumber = %s AND filename LIKE %s" )
            #sqlcheckvars = ( CallNumber, '%'+this_file['name']+'%' )
            #cursor.execute(sqlcheck,sqlcheckvars)
            #sqlcheckrow = cursor.fetchone()
            #textlength = ""
            #if (sqlcheckrow):
            #    textlength = sqlcheckrow[0]
            #if (textlength and int(textlength) > 0):
            #    print("textlength = "+str(textlength)+" -- we've already parsed it")
            #else:
            #    #let's consider parsing this file and inserting the contents into the DB
            text = "" # this is only populated if we're reading PDFs
            if read_pdfs:
                if this_file['name'][-4:].lower() == ".pdf":
                    #print('***PDF: '+folderbase+"/"+folder_name+"/"+this_file['name'])
                    
                    pdf_file_client = ShareFileClient.from_connection_string(conn_str=connection_string, share_name="torontobids", file_path=folderbase+"/"+folder_name+"/"+this_file['name'])
                    with open("temp.pdf", "wb") as pdf_file_handle:
                        pdf_data = pdf_file_client.download_file()
                        pdf_data.readinto(pdf_file_handle)

                    if (os.stat("temp.pdf")[stat.ST_SIZE] > 0):
                        reader = PdfReader("temp.pdf")
                        number_of_pages = len(reader.pages)
                        page_range = range(number_of_pages)
                        for this_page in page_range:
                            page = reader.pages[this_page]
                            text += page.extract_text()
                            foldertext += page.extract_text()
                            #print(text)
                        print("**** " + str(len(text)) +" bytes")

            #attachments[this_file['name']] = text
            sqlreal = ( "INSERT INTO attachments (CallNumber,filename,parsedtext,uuid) VALUES(%s,%s,%s,UUID()) ON DUPLICATE KEY UPDATE CallNumber = %s,filename = %s,parsedtext = %s" )
            filename_for_db = folder_name+"/"+this_file['name']
            filename_for_db = filename_for_db.replace(CallNumber+"/","")
            CallNumber_for_db = CallNumber.replace("Doc","")
            print("INSERTING attachment: "+filename_for_db)
            sqlval = ( CallNumber_for_db, filename_for_db, text, CallNumber_for_db, filename_for_db, text )
            cursor.execute(sqlreal,sqlval)
            conn.commit()

    #for this_attachment_filename,this_attachment_text in attachments.items():
        #sqlreal = ( "INSERT INTO attachments (CallNumber,filename,parsedtext,uuid) VALUES(%s,%s,%s,UUID()) ON DUPLICATE KEY UPDATE CallNumber = %s,filename = %s,parsedtext = %s" )
        #filename_for_db = folder_name+"/"+this_attachment_filename
        #filename_for_db = filename_for_db.replace(CallNumber+"/","")
        #print("INSERTING attachment: "+filename_for_db)
        #sqlval = ( CallNumber, filename_for_db, this_attachment_text, CallNumber, folder_name+"/"+this_attachment_filename, this_attachment_text )
        #cursor.execute(sqlreal,sqlval)
        #conn.commit()


    if subfolder == 0:
        if jsonfiles and jsonfiles[0]:
            jsonfiles.sort(reverse=True)
            print("reading: " + folderbase+"/"+folder_name+"/"+jsonfiles[0])

            file_client = ShareFileClient.from_connection_string(conn_str=connection_string, share_name="torontobids", file_path=folderbase+"/"+folder_name+"/"+jsonfiles[0])
            filecontents = file_client.download_file()

            y = json.loads(filecontents.readall())

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

            CallNumber_for_db = y['CallNumber'].replace("Doc","")
            #SQL INSERT
            print(f"MYSQL INSERT INTO calls -- foldertext length: {len(foldertext)}")
            sqlreal = ( "INSERT INTO calls (Commodity,CommodityType,CallNumber,Type,ShortDescription,Description,ShowDatePosted,ClosingDate,SiteMeeting,ShowBuyerNameList,BuyerPhoneShow,BuyerEmailShow,Division,BuyerLocationShow,parsedtext,uuid) " + 
            "VALUES(%s,%s,%s,%s,%s,%s,STR_TO_DATE(%s,'%M %d, %Y'),STR_TO_DATE(%s,'%M %d, %Y'),%s,%s,%s,%s,%s,%s,%s,UUID())" +
            " ON DUPLICATE KEY UPDATE Commodity = %s,CommodityType = %s,Type = %s,ShortDescription = %s,Description = %s,ShowDatePosted = STR_TO_DATE(%s,'%M %d, %Y'),ClosingDate = STR_TO_DATE(%s,'%M %d, %Y'),SiteMeeting = " + 
            "%s,ShowBuyerNameList = %s,BuyerPhoneShow = %s,BuyerEmailShow = %s,Division = %s,BuyerLocationShow = %s,parsedtext = %s;")
            sqlval = ( y['Commodity'], y['CommodityType'],CallNumber_for_db, y['Type'], y['ShortDescription'], y['Description'], y['ShowDatePosted'], y['ClosingDate'], y['SiteMeeting'], y['ShowBuyerNameList'], y['BuyerPhoneShow'], y['BuyerEmailShow'], y['Division'] , y['BuyerLocationShow'], foldertext, y['Commodity'], y['CommodityType'] , y['Type'] , y['ShortDescription'] , y['Description'] , y['ShowDatePosted'] , y['ClosingDate'], y['SiteMeeting'] , y['ShowBuyerNameList'] , y['BuyerPhoneShow'] , y['BuyerEmailShow'] , y['Division'] , y['BuyerLocationShow'], foldertext )
            cursor.execute(sqlreal,sqlval)
            conn.commit()
        else: 
            print("HMMM -- THERE PROBABLY SHOULD'VE BEEN A JSON FILE IN THERE!")






    print("-- Ending folder: " + folder_name)
    return(foldertext)






#########################################


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
    if (this_folder_data["name"] >= folder_to_start_with) and (this_folder_data["name"] not in folders_to_skip) and (this_folder_data["is_directory"]): 
        process_folder(this_folder_data['name'],this_folder_data['name'],0)


