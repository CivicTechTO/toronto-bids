CREATE TABLE type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, type VARCHAR(256) NOT NULL
);

CREATE TABLE commodity
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity VARCHAR(256) NOT NULL
);

CREATE TABLE commodity_type
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, commodity_id INT  NOT NULL
	, commodity_type VARCHAR(256)  NOT NULL

	, FOREIGN KEY (commodity_id) REFERENCES commodity(id)
);

CREATE TABLE division
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, division VARCHAR(256) NOT NULL
);

CREATE TABLE location
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, location VARCHAR(256) NOT NULL
);

CREATE TABLE buyer
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, name VARCHAR(256) NOT NULL
	, phone VARCHAR(256) NOT NULL
	, email VARCHAR(256) NOT NULL
	, location_id INT NOT NULL

	, FOREIGN KEY (location_id) REFERENCES location(id)
);

CREATE TABLE document
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, type_id INT NOT NULL
	, call_number VARCHAR(30) NOT NULL
	, commodity_type_id INT NOT NULL
	, division_id INT NOT NULL
	, short_description VARCHAR(256) NOT NULL
	, description VARCHAR(10000) NOT NULL
	, posting_date DATE NOT NULL
	, closing_date DATE NOT NULL
	, site_meeting VARCHAR(1000) NOT NULL
	, last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP

	, FOREIGN KEY (type_id) REFERENCES type(id)
	, FOREIGN KEY (commodity_type_id) REFERENCES commodity_type(id)
	, FOREIGN KEY (division_id) REFERENCES division(id)
);

CREATE TABLE document_buyer
(
	document_id INT NOT NULL
	, buyer_id INT NOT NULL

	, PRIMARY KEY (document_id, buyer_id)
);

CREATE TABLE search_text
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, document_id INT NOT NULL
	, search_text VARCHAR(10000) NOT NULL
);

CREATE TABLE attachment
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, document_id INT NOT NULL
	, contents LONGBLOB NOT NULL

	, FOREIGN KEY (document_id) REFERENCES document(id)
);

CREATE TABLE header
(
	id INT PRIMARY KEY AUTO_INCREMENT
	, attachment_id INT NOT NULL
	, header VARCHAR(1000) NOT NULL
);

CREATE FULLTEXT INDEX short_description_index ON document(short_description);
CREATE FULLTEXT INDEX description_index ON document(description);
CREATE FULLTEXT INDEX search_text_index ON search_text(search_text);
