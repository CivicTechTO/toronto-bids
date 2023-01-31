CREATE TABLE type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, type VARCHAR(256) NOT NULL
);

INSERT INTO type (type) SELECT DISTINCT Type FROM import.fromxml;

CREATE TABLE commodity
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity VARCHAR(256) NOT NULL
);

INSERT INTO commodity (commodity) SELECT DISTINCT Commodity FROM import.fromxml;

CREATE TABLE commodity_type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity_id INT  NOT NULL
	, commodity_type VARCHAR(256)  NOT NULL
	, FOREIGN KEY (commodity_id) REFERENCES commodity(id)
);

INSERT INTO commodity_type (commodity_type, commodity_id) SELECT DISTINCT source.commodity_type, source.commodity_id FROM 
	(
		SELECT DISTINCT 
			commodity.id AS commodity_id 
			, import.fromxml.CommodityType AS commodity_type
		FROM commodity JOIN import.fromxml ON commodity.commodity = import.fromxml.Commodity
	) AS source
;

CREATE TABLE division
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, division VARCHAR(256) NOT NULL
);

INSERT INTO division (division) SELECT DISTINCT Division FROM import.fromxml;

CREATE TABLE location
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, location VARCHAR(256) NOT NULL
);

INSERT INTO location (location) SELECT DISTINCT BuyerLocationShow FROM import.fromxml;

CREATE TABLE buyer
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, name VARCHAR(256) NOT NULL
	, phone VARCHAR(256) NOT NULL
	, email VARCHAR(256) NOT NULL
	, location_id INT NOT NULL
	, FOREIGN KEY (location_id) REFERENCES location(id)
);

INSERT INTO buyer (name, phone, email, location_id) SELECT DISTINCT source.name, source.phone, source.email, source.location_id FROM 
	(
		SELECT DISTINCT 
			location.id AS location_id
			, import.fromxml.ShowBuyerNameList AS name
			, import.fromxml.BuyerPhoneShow AS phone
			, import.fromxml.BuyerEmailShow AS email
		FROM location JOIN import.fromxml ON location.location = import.fromxml.BuyerLocationShow
	) AS source;

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
	, search_text VARCHAR(65535) NOT NULL
	, last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
	, FOREIGN KEY (type_id) REFERENCES type(id)
	, FOREIGN KEY (commodity_type_id) REFERENCES commodity_type(id)
	, FOREIGN KEY (division_id) REFERENCES division(id)
	, FOREIGN KEY (buyer_id) REFERENCES buyer(id)
);

INSERT INTO procurement (type_id, call_number, commodity_type_id, division_id, buyer_id, short_description, description, posting_date, closing_date, site_meeting, search_text)
	SELECT DISTINCT 
		source.type_id, source.call_number, source.commodity_type_id, source.division_id, source.buyer_id, source.short_description, source.description
			, source.posting_date, source.closing_date, source.site_meeting,  source.search_text
		FROM
		(
			SELECT DISTINCT 
				type.id AS type_id
				, import.fromxml.CallNumber AS call_number
				, commodity_type.id AS commodity_type_id
				, division.id AS division_id
				, buyer.id AS buyer_id
				, import.fromxml.ShortDescription AS short_description
				, import.fromxml.Description AS description
				, import.fromxml.ShowDatePosted AS posting_date
				, import.fromxml.ClosingDate AS closing_date
				, import.fromxml.SiteMeeting AS site_meeting
				, import.fromxml.parsedtext AS search_text
			FROM
				import.fromxml INNER JOIN type ON import.fromxml.Type = type.type
					INNER JOIN commodity_type ON import.fromxml.CommodityType = commodity_type.commodity_type
						INNER JOIN division ON import.fromxml.Division = division.division
							INNER JOIN buyer ON import.fromxml.ShowBuyerNameList = buyer.name
		) AS source;

CREATE FULLTEXT INDEX short_description_index ON procurement(short_description);
CREATE FULLTEXT INDEX description_index ON procurement(description);
CREATE FULLTEXT INDEX search_text_index ON procurement(search_text);
