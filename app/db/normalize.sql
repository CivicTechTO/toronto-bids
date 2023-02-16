
INSERT INTO type (type) SELECT DISTINCT Type FROM import.fromxml;

INSERT INTO commodity (commodity) SELECT DISTINCT Commodity FROM import.fromxml;

INSERT INTO commodity_type (commodity_type, commodity_id) SELECT DISTINCT source.commodity_type, source.commodity_id FROM 
	(
		SELECT DISTINCT 
			commodity.id AS commodity_id 
			, import.fromxml.CommodityType AS commodity_type
		FROM commodity JOIN import.fromxml ON commodity.commodity = import.fromxml.Commodity
	) AS source
;

INSERT INTO division (division) SELECT DISTINCT Division FROM import.fromxml;

INSERT INTO location (location) SELECT DISTINCT BuyerLocationShow FROM import.fromxml;

INSERT INTO buyer (name, phone, email, location_id) SELECT DISTINCT source.name, source.phone, source.email, source.location_id FROM 
	(
		SELECT DISTINCT 
			location.id AS location_id
			, import.fromxml.ShowBuyerNameList AS name
			, import.fromxml.BuyerPhoneShow AS phone
			, import.fromxml.BuyerEmailShow AS email
		FROM location JOIN import.fromxml ON location.location = import.fromxml.BuyerLocationShow
	) AS source
;

INSERT INTO procurement (type_id, call_number, commodity_type_id, division_id, short_description, description, posting_date, closing_date, site_meeting)
	SELECT DISTINCT 
		source.type_id, source.call_number, source.commodity_type_id, source.division_id, source.short_description, source.description
			, source.posting_date, source.closing_date, source.site_meeting
		FROM
		(
			SELECT DISTINCT 
				type.id AS type_id
				, import.fromxml.CallNumber AS call_number
				, commodity_type.id AS commodity_type_id
				, division.id AS division_id
				, import.fromxml.ShortDescription AS short_description
				, import.fromxml.Description AS description
				, STR_TO_DATE(import.fromxml.ShowDatePosted, '%M %e, %Y') AS posting_date
				, STR_TO_DATE(import.fromxml.ClosingDate, '%M %e, %Y') AS closing_date
				, import.fromxml.SiteMeeting AS site_meeting
			FROM
				import.fromxml INNER JOIN type ON import.fromxml.Type = type.type
					INNER JOIN commodity_type ON import.fromxml.CommodityType = commodity_type.commodity_type
						INNER JOIN division ON import.fromxml.Division = division.division
		) AS source
;

INSERT INTO procurement_buyer (procurement_id, buyer_id)
	SELECT DISTINCT source.procurement_id, source.buyer_id
	FROM
	(
		SELECT DISTINCT 
			procurement.id AS procurement_id
			, buyer.id AS buyer_id
		FROM 
			import.fromxml JOIN procurement ON import.fromxml.CallNumber = procurement.call_number
				INNER JOIN buyer ON import.fromxml.ShowBuyerNameList = buyer.name
	) AS source
;

INSERT INTO search_text (procurement_id, search_text)
	SELECT source.procurement_id, source.search_text 
	FROM
	(
		SELECT DISTINCT 
			procurement.id AS procurement_id
			, import.fromxml.parsedtext AS search_text
		FROM
			import.fromxml JOIN procurement ON import.fromxml.CallNumber = procurement.call_number
	) AS source
;


