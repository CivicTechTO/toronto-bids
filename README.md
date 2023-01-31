# Toronto Bids Project

The Toronto Bids Project is an initiative aimed at promoting transparency and accountability in the procurement process of the Toronto City Council. The project involves scraping request for proposal (RFP) documents from the Toronto City Council's procurement tool, SAP Ariba, and making the information publicly accessible through a database. The goal is to make the procurement process more accessible to the public even after the due date of the RFPs has passed.

# Project Outline

The project consists of the following main stages:

## Scraping

The first stage of the project involves scraping RFP information from the Toronto City Council's procurement tool, SAP Ariba. A custom-built scraping tool has been developed to crawl the SAP Ariba website and extract relevant information about each RFP, including structured metadata and PDF documents containing detailed information.

In addition, the city posts some metadata about the RFPs through the Open Data Portal, and a separate scraper has been developed to capture this information.

## Ingestion

The next stage of the project involves ingestion of the extracted information into a database. The downloaded data consists of structured metadata as well as PDF documents containing additional information. A separate script is used to extract human-readable text from the PDF documents, which is then added to the main table of data.

The extracted information is then stored in an SQL database for easier management and accessibility.
## Making the data public

The final stage of the project involves making the information publicly accessible through a user-friendly website. The goal is to provide an easy-to-use platform for accessing the information contained in the RFPs, even after the due date has passed, promoting transparency and accountability in the procurement process of the Toronto City Council.

This stage also requires making an API to allow programmatic access to the database of RFP information. This API will allow developers to programmatically access and retrieve data from the database for use in their own applications and tools. The API should include endpoints for retrieving information about specific RFPs, searching for RFPs based on certain criteria, and retrieving metadata about the RFPs. The API will be a critical component of the project, allowing the data to be used in a wide range of applications and promoting the goal of transparency and accessibility in the Toronto City Council's procurement process.
