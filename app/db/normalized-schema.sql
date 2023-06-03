CREATE TABLE type (
	id INT PRIMARY KEY AUTO_INCREMENT,
	type VARCHAR(256) NOT NULL,
	UNIQUE (type)
);

CREATE TABLE commodity (
	id INT PRIMARY KEY AUTO_INCREMENT,
	commodity VARCHAR(256) NOT NULL,
	UNIQUE (commodity)
);

CREATE TABLE commodity_type (
	id INT PRIMARY KEY AUTO_INCREMENT,
	commodity_id INT NOT NULL,
	commodity_type VARCHAR(256) NOT NULL,
	UNIQUE (commodity_id, commodity_type),
	FOREIGN KEY (commodity_id) REFERENCES commodity(id)
);

CREATE TABLE division (
	id INT PRIMARY KEY AUTO_INCREMENT,
	division VARCHAR(256) NOT NULL,
	UNIQUE (division)
);

CREATE TABLE location (
	id INT PRIMARY KEY AUTO_INCREMENT,
	location VARCHAR(256) NOT NULL,
	UNIQUE (location)
);

CREATE TABLE buyer (
	id INT PRIMARY KEY AUTO_INCREMENT,
	buyer VARCHAR(256) NOT NULL,
	phone VARCHAR(256) NOT NULL,
	email VARCHAR(256) NOT NULL,
	location_id INT NOT NULL,
	FOREIGN KEY (location_id) REFERENCES location(id)
);

CREATE TABLE document (
	id INT PRIMARY KEY AUTO_INCREMENT,
	type_id INT NOT NULL,
	call_number VARCHAR(30) NOT NULL,
	commodity_type_id INT NOT NULL,
	division_id INT NOT NULL,
	short_description VARCHAR(256) NOT NULL,
	description TEXT NOT NULL,
	search_text LONGTEXT NOT NULL,
	posting_date DATE NOT NULL,
	closing_date DATE NOT NULL,
	site_meeting VARCHAR(1000) NOT NULL,
	last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FULLTEXT (short_description, description, search_text),
	FOREIGN KEY (type_id) REFERENCES type(id),
	FOREIGN KEY (commodity_type_id) REFERENCES commodity_type(id),
	FOREIGN KEY (division_id) REFERENCES division(id)
);

CREATE TABLE document_buyer (
	document_id INT NOT NULL,
	buyer_id INT NOT NULL,
	PRIMARY KEY (document_id, buyer_id)
);

CREATE TABLE attachments (
	call_number varchar(30) NOT NULL,
	filename varchar(1000) NOT NULL
);