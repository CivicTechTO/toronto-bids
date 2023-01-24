CREATE TABLE type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, type VARChar(256) NOT NULL
);

INSERT INTO type (type) SELECT DISTINCT Type FROM import.fromxml;

CREATE TABLE commodity
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity VARChar(256) NOT NULL
);

INSERT INTO commodity (commodity) SELECT DISTINCT Commodity FROM import.fromxml;

CREATE TABLE commodity_type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity_id INT  NOT NULL
	, commodity_type VARChar(256)  NOT NULL
);

INSERT INTO commodity_type (commodity_type, commodity_id) SELECT DISTINCT source.commodity_type, source.commodity_id FROM 
	(
		SELECT DISTINCT 
			id AS commodity_id 
			, import.fromxml.CommodityType AS commodity_type
		FROM commodity JOIN import.fromxml ON commodity.commodity = import.fromxml.Commodity
	) AS source
;

CREATE TABLE division
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, division VARChar(256) NOT NULL
);

INSERT INTO division (division) SELECT DISTINCT Division FROM import.fromxml;

CREATE TABLE location
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, location VARChar(256) NOT NULL
);

INSERT INTO location (location) SELECT DISTINCT Location FROM import.fromxml;

CREATE TABLE buyer
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, name VARChar(256) NOT NULL
	, phone VARChar(256) NOT NULL
	, email VARChar(256) NOT NULL
	, location_id INT NOT NULL
);

INSERT INTO buyer (name, phone, email, location_id) SELECT DISTINCT source.name, source.phone, source.email, source.location_id FROM 
	(
		SELECT DISTINCT 
			id AS location_id
			, import.fromxml.ShowBuyerNameList AS name
			, import.fromxml.BuyerPhoneShow AS phone
			, import.fromxml.BuyerEmailShow AS email
		FROM location JOIN import.fromxml ON location.location = import.fromxml.location
	) AS source
;

CREATE TABLE procurement
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, type_id INT NOT NULL
	, call_number VARCHAR(30) NOT NULL
	, commodity_type_id INT NOT NULL
	, division_id INT NOT NULL
	, buyer_id INT NOT NULL
	, short_description VARCHAR(256) NOT NULL
	, description VARCHAR(10000) NOT NULL
	, posting_date DATE NOT NULL
	, closing_date DATE NOT NULL
	, site_meeting VARCHAR(1000) NOT NULL
	, search_text VARCHAR(65536) NOT NULL
	, last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP

);

INSERT INTO procurement (type_id, call_number, commodity_type_id, division_id, buyer_id, short_description, description, posting_date, closing_date, site_meeting, search_text)
	SELECT DISTINCT 