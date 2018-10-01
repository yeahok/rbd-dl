import base64
import sys
import http.cookiejar
import shutil
import time
import pathlib
import hashlib
import subprocess
import os
import simplejson as json

import requests
import fitz
from PyPDF2 import PdfFileWriter, PdfFileReader
from bs4 import BeautifulSoup

BASE_URL = "https://www.rbdigital.com/ajaxd.php"
#will be initialised on runtime lmao
USERNAME = ""
PASSWORD = ""
SECRET_PW = ""
LANDING_PAGE = ""
LIB_ID = ""


#find text between item1 and item2 in string. this function is terrible
def find_between(string, item1, item2 = False):
    index1 = string.find(item1) + len(item1)
    if (item2):
        index2 = string.find(item2)
        return string[index1:index2]
    else:
        return string[index1:]

#format date from words to numbers in YYYY-MM-DD format
def format_date(date):
    date = date.replace(",", "")
    struct_time = time.strptime(date, "%B %d %Y")

    return time.strftime("%Y %m %d", struct_time).replace(' ', '-')

#get decryption pass
def calculate_pass(legacy_issue_id):
    binaryPass = SECRET_PW.encode('ascii')

    m = hashlib.sha1()
    m.update(binaryPass)
    m.update(b"_")
    m.update(legacy_issue_id.encode())
    
    return m.hexdigest()    

#because there are special pages like C2
def get_page_no(page_id, pagesInfo):
    #print(pagesInfo[0])
    for i in range(0, len(pagesInfo)):

        if pagesInfo[i]["folio_number"] == page_id:
            #needs offset by one if using pymupdf toc
            return i

#adds toc
def add_toc(sectionsInfo, pagesInfo, filename, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"  
    
    output = PdfFileWriter()

    fileread = open(path + filename, "rb")
    inputPdf = PdfFileReader(fileread)
    
    output.appendPagesFromReader(inputPdf)
    
    for i in range(0, len(sectionsInfo)):
        parentTitle = sectionsInfo[i]["name"]
        parentPage = sectionsInfo[i]["stories"][0]["starting_page"]
        parentPage = get_page_no(parentPage, pagesInfo)

        parentBookmark = output.addBookmark(parentTitle, parentPage)

        #print(parentTitle + " page: " + str(parentPage))
        
        for j in range(0, len(sectionsInfo[i]["stories"])):
            title = sectionsInfo[i]["stories"][j]["title"]
            page = sectionsInfo[i]["stories"][j]["starting_page"]
            page = get_page_no(page, pagesInfo)

            #sometimes there's a dupe toc entry so maybe it should be ignored
            if parentTitle.lower() == title.lower():
                break

            output.addBookmark(title, page, parent=parentBookmark)
    
    with open(outputpath + outputfilename, 'wb') as f:
        output.write(f)

    #not sure where this needs to be but it works here
    fileread.close()

def remove_links_annots(filename, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"  

    
    output = PdfFileWriter()
    fileread = open(path + filename, "rb")
    inputPdf = PdfFileReader(fileread)
    #inputPdf = PdfFileReader(open(path + filename, "rb"))
       
    output.appendPagesFromReader(inputPdf)

    output.removeLinks()

    with open(outputpath + outputfilename, 'wb') as f:
        output.write(f)

    fileread.close()
        
def add_links(pagesInfo, filename, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"

    doc = fitz.open(path + filename)

    print(pagesInfo[0])
    working = 0
    
    for i in range(0, len(pagesInfo)):
        if pagesInfo[i]["links"]:
            working = 1
            pageHeight = doc[i].rect.y1

            for j in range(0, len(pagesInfo[i]["links"])):
                linktype = pagesInfo[i]["links"][j]["type"]
                coords = pagesInfo[i]["links"][j]["coordinates"].split(",")
                href = pagesInfo[i]["links"][j]["href"]
                for y in range(0, 4):
                    coords[y] = float(coords[y])
                
                link = {}
                link["from"] = fitz.Rect(coords[0], pageHeight - coords[3],coords[2],pageHeight - coords[1])
                
                if linktype == "page":
                    link["kind"] = fitz.LINK_GOTO
                    link["type"] = "goto"
                    link["page"] = int(href) - 3
                elif linktype == "external":
                    link["kind"] = fitz.LINK_URI
                    link["type"] = "uri"
                    link["uri"] = href
                    
                #print(href)
                doc[i].insertLink(link)
                #print(linktype)
                #print(coordinates)

    if working == 0:
        sys.exit(1)

    doc.save(outputpath + outputfilename)
    doc.close()

#parsing magazine titles from a single page
def parse_magazines(html):
    soup = BeautifulSoup(html, 'html.parser')
    tags = soup.find_all("div", class_="magazine-card")

    issue_list = []
    #print("found " + str(len(tags)) + " tags")
    for i in range(0, len(tags)):
        issueId = find_between(tags[i].a["href"], "reader/", "?zenith")
        date = find_between(tags[i].a["title"], "Read ", " issue of")
        formatted_date = format_date(date)

        title = find_between(tags[i].a["title"], "issue of ")
    
        info = {
            "id": issueId,
            "date": formatted_date,
            "title": title,
            "thumbnail_url": tags[i].a.img["src"],
	    "full_url": "https://www.rbdigital.com" + tags[i].a["href"]
        }
        issue_list.append(info)
    return issue_list

#parsing magazine titles from all pages
def get_issue_list(session):
    issueForm = {
        "content_filter": "",
        "lib_id": LIB_ID,
        "p_num": "0",
        "service_t": "magazines"
    }

    getIssuesOption = {"action": "zinio_user_issue_collection"}
    
    issues = []

    current_page = 0
    parsing_collection = True

    while parsing_collection:
        #for some reason page 0 and 1 are the same so page 1 will be skipped
        if current_page == 1:
            current_page += 1

        print("browsing page:" + str(current_page))
    
        issueForm["p_num"] = str(current_page)
    
        issueRequest = session.post(BASE_URL, params = getIssuesOption, data = issueForm)
        htmlResponse = base64.b64decode(issueRequest.json()["content"]).decode('UTF-8')

        foundData = not (htmlResponse.find("no data") + 1)
        
        if (foundData):
            issues += parse_magazines(htmlResponse)
            current_page += 1
        else:
            parsing_collection = False

    return issues

#get auth from reader page
def get_auth(reader_url, session):
    reader_request = session.get(reader_url, allow_redirects=True)
    soup = BeautifulSoup(reader_request.text, 'html.parser')
    print(reader_request.status_code)

    
    js = soup.findAll('script')[4].string
    js2 = soup.findAll('script')[5].string

    auth = {
        "auth_code": find_between(js, "', '", "');"),
        "user_id": find_between(js, "InitZinioReader( '", "', '"),
        "newsstand_id": find_between(js2, 'NEWSSTAND_ID":', ',"ASSETS_PATH')
    }
    return auth

#adapted from https://stackoverflow.com/a/39217788
def download_file(url, filename, subdirectory = False):
    path = "./"
    if (subdirectory):
        pathlib.Path('./' + subdirectory + "/").mkdir(exist_ok=True) 
        path = "./" + subdirectory + "/"
        
    local_filename = filename
    r = requests.get(url, stream=True)
    with open(path + local_filename, 'wb') as f:
        shutil.copyfileobj(r.raw, f)

    return local_filename

def load_settings_file(filename):
    if not os.path.isfile(filename):
        print(filename + " not found")
        return []
    
	#i heard json has a proper method for loading files but
    with open(filename) as infile:
        data = json.load(infile)
        return data

def save_settings_file(data, filename):
    with open(filename, 'w') as outfile:
        json.dump(data, outfile)

def list_difference(list1, list2):
    difference = set(list1) - set(list2)
    return list(difference)

def decrypt_pdf(filename, pw, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"   
    run = subprocess.run("pdftk " + path + filename + " input_pw " + pw + " output " + outputpath + outputfilename, capture_output=True, shell=True)
    run.check_returncode()

def merge_pdf(outputfile, subdirectory, outputsubdirectory):
    command = "pdftk ./" + subdirectory + "/decrypt*.pdf cat output ./" + outputsubdirectory + "/" + outputfile
    run = subprocess.run(command, capture_output=True, shell=True)
    run.check_returncode()

#this is a hacky way of removing the toc and should probably be replaced
def remove_toc(inputfile, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"   
    command = "pdftk " + path + inputfile + " cat 1-end output " + outputpath + outputfilename 
    run = subprocess.run(command, capture_output=True, shell=True)
    run.check_returncode()

#for some reason the last page is the first page on full pdfs so this is needed probably. also removes toc
def fix_full_pdf_order(inputfile, outputfilename, subdirectory = False, outputsubdirectory = False):
    path = "./"
    outputpath = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    if outputsubdirectory:
        outputpath = outputpath + outputsubdirectory + "/"
    
    command = "pdftk ./" + subdirectory + "/" + inputfile + " cat 2-end 1 output " + outputpath + outputfilename
    run = subprocess.run(command, capture_output=True, shell=True)
    run.check_returncode()  

def delete_file(file, subdirectory = False):
    path = "./"
    if subdirectory:
        path = path + subdirectory + "/"
    
    if os.path.isfile(path + file):
        os.remove(path + file)
    else:
        print("Error: %s file not found" % file)

def getIssueInfo(issue, auth, session):
    API_BASE_URL = "https://api-sec.ziniopro.com/newsstand/v2/newsstands/" + auth["newsstand_id"] + "/issues/"

    authheaders = {
        'authorization': 'Bearer ' + auth["auth_code"],
        'x-zinio-user-id': auth["user_id"]
    }

    issueInfoRequest = session.get(API_BASE_URL + issue["id"], headers = authheaders)
    json = issueInfoRequest.json()

    return json["data"]

def getPagesInfo(issue, auth, session, pdf = False):
    if pdf:
        pagesInfoOption = {"format": "pdf", "application": "undefined"}
    else:
        pagesInfoOption = {"format": "svg", "application": "undefined"}

    API_BASE_URL = "https://api-sec.ziniopro.com/newsstand/v2/newsstands/" + auth["newsstand_id"] + "/issues/"

    authheaders = {
        'authorization': 'Bearer ' + auth["auth_code"],
        'x-zinio-user-id': auth["user_id"]
    }

    pagesInfoRequest = session.get(API_BASE_URL + issue["id"] + "/content/pages", params = pagesInfoOption, headers = authheaders)
    json = pagesInfoRequest.json()

    return json["data"]

def getSectionsInfo(issue, auth, session):
    API_BASE_URL = "https://api-sec.ziniopro.com/newsstand/v2/newsstands/" + auth["newsstand_id"] + "/issues/"

    authheaders = {
        'authorization': 'Bearer ' + auth["auth_code"],
        'x-zinio-user-id': auth["user_id"]
    }

    sectionsInfoRequest = session.get(API_BASE_URL + issue["id"] + "/sections", headers = authheaders)
    json = sectionsInfoRequest.json()

    return json["data"]

#checks if full pdf url works
def full_pdf_working(url):
    header = requests.head(url)
    return (header.status_code == 206) or (header.status_code == 200)

#downloads issue
def download_issue(issue, auth, session):
    issueInfo = getIssueInfo(issue, auth, session)
    pagesInfo = getPagesInfo(issue, auth, session, pdf = False)
    pdfPagesInfo = getPagesInfo(issue, auth, session, pdf = True)
    sectionsInfo = getSectionsInfo(issue, auth, session)
    
    full_pdf_url = issueInfo["issue_content"]["full_pdf"]

    pw = calculate_pass(str(issueInfo["legacy_issue_id"]))

    filename = issue["title"] + " " + issue["date"] + ".pdf"

    #remove bad characters
    bad = ":+"
    for char in bad:
        filename = filename.replace(char, "")

    if full_pdf_working(full_pdf_url):
        download_full_issue(full_pdf_url, sectionsInfo, pagesInfo, pw, filename)
        print("download done: " + filename)
        return
    else:
        download_split_issue(issue, pagesInfo, pdfPagesInfo, sectionsInfo, pw, auth, session, filename)
        print("download done: " + filename)
        return

def download_full_issue(full_pdf_url, sectionsInfo, pagesInfo, pw, filename):
        print("downloading: " + filename)
        download_file(full_pdf_url, "temp1.pdf", "temp_dir")
        decrypt_pdf("temp1.pdf", pw, "temp2.pdf", "temp_dir", "temp_dir")
        fix_full_pdf_order("temp2.pdf", "temp3.pdf", "temp_dir", "temp_dir")
        remove_links_annots("temp3.pdf", "temp4.pdf", "temp_dir", "temp_dir")
        add_toc(sectionsInfo, pagesInfo, "temp4.pdf", "temp5.pdf", "temp_dir", "temp_dir")
        #final output
        add_links(pagesInfo, "temp5.pdf", filename, subdirectory = "temp_dir")

        delete_file("temp1.pdf", "temp_dir")
        delete_file("temp2.pdf", "temp_dir")
        delete_file("temp3.pdf", "temp_dir")
        delete_file("temp4.pdf", "temp_dir")
        delete_file("temp5.pdf", "temp_dir")

#downloads split issue. i should probably learn how to code sometime
def download_split_issue(issue, pagesInfo, pdfPagesInfo, sectionsInfo, pw, auth, session, filename):
    print("downloading: " + filename)
    print("parts: " + str(len(pdfPagesInfo)))

    for i in range(0, len(pdfPagesInfo)):
        #zfill is padding numbers
        inputfilename = "temp" + str(i).zfill(4) + ".pdf"
        download_file(pdfPagesInfo[i]["src"], inputfilename, "temp_dir")
                  
        outputfilename = "decrypt" + str(i).zfill(4) + ".pdf"
        decrypt_pdf(inputfilename, pw, outputfilename, "temp_dir", "temp_dir")

    merge_pdf("temp1.pdf", "temp_dir", "temp_dir")
    remove_toc("temp1.pdf", "temp2.pdf", "temp_dir", "temp_dir")
    remove_links_annots("temp2.pdf", "temp3.pdf", "temp_dir", "temp_dir")
    add_toc(sectionsInfo, pagesInfo, "temp3.pdf", "temp4.pdf", "temp_dir", "temp_dir")
    #final output
    add_links(pagesInfo, "temp4.pdf", filename, subdirectory = "temp_dir")

    #delete temp files
    for i in range(0, len(pagesInfo)):
        inputfilename = "temp" + str(i).zfill(4) + ".pdf"
        delete_file(inputfilename, "temp_dir")
          
        outputfilename = "decrypt" + str(i).zfill(4) + ".pdf"
        delete_file(outputfilename, "temp_dir")
    delete_file("temp1.pdf", "temp_dir")
    delete_file("temp2.pdf", "temp_dir")
    delete_file("temp3.pdf", "temp_dir")
    delete_file("temp4.pdf", "temp_dir")

#downloads all issues. also checks if already downloaded
def download_all_issues(issues, auth, session):
    downloadHistory = load_settings_file("dlhistory.txt")
    downloadList = []
    for i in range(0,len(issues)):
        downloadList.append(issues[i]["id"])
    downloadList = list_difference(downloadList, downloadHistory)

    if (len(downloadList) == 0):
        print("no new issues to download")
        sys.exit("no new issues to download")

    print("download history")
    for i in range(0,len(downloadHistory)):
        print(downloadHistory[i])

    print("download list")
    for i in range(0,len(downloadList)):
        print(downloadList[i])
    print("download list end")

    #this is absolutely terrible
    for i in range(0,len(downloadList)):
        for j in range(0,len(issues)):
            if (downloadList[i] == issues[j]["id"]):
                download_issue(issues[j], auth, session)
                downloadHistory.append(downloadList[i])
                #print(issues[j]["title"])
                save_settings_file(downloadHistory, "dlhistory.txt")
                break

    print("downloads done. total downloaded: " + str(len(downloadList)))

def getLibID(landing_page_url):
    homepage = requests.get(landing_page_url)

    soup = BeautifulSoup(homepage.text, 'html.parser')
    js = soup.findAll('script')[11].string

    lib_id = find_between(js, "g_nLibraryId = ", ";\n-->")
    
    return lib_id
	
def login(username, password, lib_id, session):
    jar = requests.cookies.RequestsCookieJar()
    jar.set("ref", base64.b64encode("memes".encode('UTF-8')).decode('UTF-8'), domain="www.rbdigital.com", path="/")
    jar.set("your_username", base64.b64encode(USERNAME.encode('UTF-8')).decode('UTF-8'), domain=".rbdigital.com", path="/")
    jar.set("zinio-locale-web", "en_US", domain="www.rbdigital.com", path="/")

    loginOption = {"action": "p_login"}
    loginForm = {
        "lib_id": lib_id,
        "password": password,
        "remember_me": "1",
        "username": username
    }

    #initial login request for cookies
    loginRequest = session.post(BASE_URL, params = loginOption, data = loginForm, cookies = jar)

    if (loginRequest.status_code == requests.codes.ok):
        print("login successful")
    else:
        print("login bad, error code:" + loginRequest.status_code)
        loginRequest.raise_for_status()
        sys.exit("login failed")
##


settings = load_settings_file("settings.cfg")
if len(settings) == 0:
	print("settings not loaded")
	sys.exit()
USERNAME = settings["username"]
PASSWORD = settings["password"]
SECRET_PW = settings["secret_pw"]
LANDING_PAGE = settings["landing_page"]

session = requests.Session()

lib_id = getLibID(LANDING_PAGE)
login(USERNAME, PASSWORD, lib_id, session)

issues = get_issue_list(session)

auth = get_auth(issues[0]["full_url"], session)

print("total issues found: " + str(len(issues)))

##download_all_issues(issues, auth, session)


