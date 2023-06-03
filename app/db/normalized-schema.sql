-- Table to contain the set of call types found from the scraper 
CREATE TABLE type (
	id INT PRIMARY KEY AUTO_INCREMENT,
	type VARCHAR(256) NOT NULL,
	UNIQUE (type)
);

-- Table to contain the set of commodity classifications found on calls
CREATE TABLE commodity (
	id INT PRIMARY KEY AUTO_INCREMENT,
	commodity VARCHAR(256) NOT NULL,
	UNIQUE (commodity)
);

-- Table to contain the set of commodity sub classifications found on calls
CREATE TABLE commodity_type (
	id INT PRIMARY KEY AUTO_INCREMENT,
	commodity_id INT NOT NULL,
	commodity_type VARCHAR(256) NOT NULL,
	UNIQUE (commodity_id, commodity_type),
	FOREIGN KEY (commodity_id) REFERENCES commodity(id)
);

-- Table to contain divisons that calls come from 
CREATE TABLE division (
	id INT PRIMARY KEY AUTO_INCREMENT,
	division VARCHAR(256) NOT NULL,
	UNIQUE (division)
);

-- Table to contain the locations of buyers 
CREATE TABLE location (
	id INT PRIMARY KEY AUTO_INCREMENT,
	location VARCHAR(256) NOT NULL,
	UNIQUE (location)
);

-- Table containing the buyers found as part of the call on a given scraper
CREATE TABLE buyer (
	id INT PRIMARY KEY AUTO_INCREMENT,
	buyer VARCHAR(256) NOT NULL, --Name of the buyer
	phone VARCHAR(256) NOT NULL, --Phone number of the buyer
	email VARCHAR(256) NOT NULL, --Email for the given buyer
	location_id INT NOT NULL, --FK to the location
	FOREIGN KEY (location_id) REFERENCES location(id)
);

--Rename this to be call or something else that better reflects what it is
--This table contains the contents of rfps/tenders/calls etc that the scraper reads from its data sources
CREATE TABLE document (
	id INT PRIMARY KEY AUTO_INCREMENT,
	type_id INT NOT NULL, -- FK to the type table 
	call_number VARCHAR(30) NOT NULL, -- (Remove this field)
	commodity_type_id INT NOT NULL, --FK To the Commodity_Type table
	division_id INT NOT NULL, -- FK To the division table
	short_description VARCHAR(256) NOT NULL, -- A Description of the rfp/tender offer/call taken from ariba
	description TEXT NOT NULL, -- Another description of the rfp/tender offer/call taken from ariba
	search_text LONGTEXT NOT NULL, -- Search text
	posting_date DATE NOT NULL,  -- The date that the call was posted
	closing_date DATE NOT NULL,-- The date that the call is due to be closed. (Can this change)?
	site_meeting VARCHAR(1000) NOT NULL, -- (Please update)
	last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, -- Timestamp
	FULLTEXT (short_description, description, search_text),
	FOREIGN KEY (type_id) REFERENCES type(id),
	FOREIGN KEY (commodity_type_id) REFERENCES commodity_type(id),
	FOREIGN KEY (division_id) REFERENCES division(id)
);

-- Join Table to associate a buyer with a Call/Document
CREATE TABLE document_buyer (
	document_id INT NOT NULL,
	buyer_id INT NOT NULL,
	PRIMARY KEY (document_id, buyer_id)
);

-- Table to contain information about about attachments on a given call
CREATE TABLE attachments (
	call_number varchar(30) NOT NULL, -- FK to the Call/Documents table
	filename varchar(1000) NOT NULL -- URL to the attachment file on the Azure Datastore
);