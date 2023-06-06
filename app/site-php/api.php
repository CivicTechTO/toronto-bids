<?php

/*
Usage:
http://pwd.ca/torontobidsarchive/api.php: 
- cn: Return detail info for the specified record
- If cn is not specified: Returns summary of all records
	Optional:
	- q: search string (just text, no quotes or anything fancy)
	- d: only display records with the specified Division
	- c: only display records with the specified Commodity
	- ct: only display records with the specified CommodityType
	- l: max number of records to return. Default = 25.
	- o: offset of records to return (for pagination)
	- sort: dateasc or datedes or random (default: datedes). Applies to ClosingDate.
	- v: If v=2, returns in modified format (default: v=1)
*/

##############

#turn these off later!
ini_set ('error_reporting', E_ALL);
ini_set ('display_errors', '1');
error_reporting (E_ALL|E_STRICT);

include 'includes/mysql-vars.php';

$mysqli = new mysqli($mysqlhost, $mysqluser, $mysqlpassword, $mysqldb);

if (!empty($_REQUEST['cn'])) {
	#return a single record
	$where = "CallNumber = ? AND ClosingDate < NOW()";
	$params[] = "s";
	$params[] = $_REQUEST['cn'];

	$stmt = $mysqli->prepare("SELECT `CallNumber`, `Commodity`, `CommodityType`, `Type`, `ShortDescription`, `Description`, `ShowDatePosted`, `ClosingDate`, `SiteMeeting`, `ShowBuyerNameList`, `BuyerPhoneShow`, `BuyerEmailShow`, `Division`, `BuyerLocationShow`, `parsedtext`, `lastupdated`, `uuid` FROM `calls` WHERE ".$where);
	$stmt->bind_param(...$params);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result

	$rows = array();
	$r = $result->fetch_assoc();
	$output = [
		"data"=>$r
	];

	$stmt = $mysqli->prepare("SELECT `filename` FROM `attachments` WHERE CallNumber = ?");
	$aparams[] = "s";
	$aparams[] = $_REQUEST['cn'];
	$stmt->bind_param(...$aparams);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result

	while ($ar = $result->fetch_assoc() ) {
		$output['data']['attachments'][] = $ar['filename'];
	}

} else {
	#return a list of records
	if (!empty($_REQUEST['o'])) {
		$offset = intval($_REQUEST['o']);
	} else {
		$offset = 0;
	}
	if (!empty($_REQUEST['l'])) {
		$limit = intval($_REQUEST['l']);
	} else {
		$limit = 25;
	}

	if (!empty($_REQUEST['q'])) {
#		$where = "parsedtext LIKE ?";
#		$params[] = "s";
#		$params[] = "%".$_REQUEST['q']."%";
		$where = "( parsedtext LIKE ? OR ShortDescription LIKE ? OR Description LIKE ? OR ShowBuyerNameList LIKE ? OR Division LIKE ? ) AND ClosingDate < CURDATE()";
		$params[] = "sssss";
		$params[] = "%".$_REQUEST['q']."%";
		$params[] = "%".$_REQUEST['q']."%";
		$params[] = "%".$_REQUEST['q']."%";
		$params[] = "%".$_REQUEST['q']."%";
		$params[] = "%".$_REQUEST['q']."%";
	} else {
		$where = "? AND ClosingDate < CURDATE()";
		$params[] = "s";
		$params[] = "1";
	}
	if (!empty($_REQUEST['c'])) {
		$where .= " AND `Commodity` = ?";
		$params[0] .= 's';
		$params[] = $_REQUEST['c'];
	}
	if (!empty($_REQUEST['ct'])) {
		$where .= " AND `CommodityType` = ?";
		$params[0] .= 's';
		$params[] = $_REQUEST['ct'];
	}
	if (!empty($_REQUEST['d'])) {
		if ($_REQUEST['d'] == "null") {
			$where .= " AND `Division` = ''";
		} else {
			$where .= " AND `Division` = ?";
			$params[0] .= 's';
			$params[] = $_REQUEST['d'];
		}
	}
	if ( (!empty($_REQUEST['sort'])) && ($_REQUEST['sort'] == "dateasc") ) {
    	$orderby = 'ORDER BY `ClosingDate` ASC';
	} elseif ( (!empty($_REQUEST['sort'])) && ($_REQUEST['sort'] == "random") ) {
		$orderby = 'ORDER BY RAND()';
	} else {
		$orderby = 'ORDER BY `ClosingDate` DESC';
	}

	$params[0] .= "ii";
	$params[] = $offset;
	$params[] = $limit;

	$stmt = $mysqli->prepare("SELECT `ShortDescription`,`CallNumber`,`ClosingDate`,`Division`,`Type`,`Commodity`,`CommodityType`,LENGTH(`parsedtext`) as textlength FROM `calls` WHERE ".$where." ".$orderby." LIMIT ?,?");
	$stmt->bind_param(...$params);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result

	$rows = array();
	while ($r = $result->fetch_assoc()) {
		$rows[] = $r;
	}
	$totalrecords = $result->num_rows;

	$stmt = $mysqli->prepare("SELECT `Commodity`,count(`Commodity`) as count FROM `calls` WHERE ".$where." GROUP BY `Commodity` ".$orderby." LIMIT ?,?");
	$stmt->bind_param(...$params);
	#$stmt->bind_param("s", $wherevalue);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result
	$top['Commodity'] = array();
	while ($r = $result->fetch_row()) {
		$top['Commodity'][$r[0]] = $r[1];
	}

	$stmt = $mysqli->prepare("SELECT `CommodityType`,count(`CommodityType`) as count FROM `calls` WHERE ".$where." GROUP BY `CommodityType` ".$orderby." LIMIT ?,?");
	$stmt->bind_param(...$params);
	#$stmt->bind_param("s", $wherevalue);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result
	$top['CommodityType'] = array();
	while ($r = $result->fetch_row()) {
		$top['CommodityType'][$r[0]] = $r[1];
	}

	$stmt = $mysqli->prepare("SELECT `Division`,count(`Division`) as count FROM `calls` WHERE ".$where." GROUP BY `Division` ".$orderby." LIMIT ?,?");
	$stmt->bind_param(...$params);
	#$stmt->bind_param("s", $wherevalue);
	$stmt->execute();
	$result = $stmt->get_result(); // get the mysqli result
	$top['Division'] = array();
	while ($r = $result->fetch_row()) {
		$top['Division'][$r[0]] = $r[1];
	}
	if ( (empty($_REQUEST['v'])) || ($_REQUEST['v'] != "2") ) {
		$output = [
			"data"=>$rows,
			"meta"=>
			[
				"total"=>$totalrecords, "limit"=>$limit, "offset"=>$offset
			],
			"included"=>
			[
				"topCommodity"=>$top['Commodity'],
				"topCommodityType"=>$top['CommodityType'],
				"topDivision"=>$top['Division']
		
			],
		];
	} else {
		$output = 
			$rows;
	}
}
header('Content-Type: application/json');
print json_encode($output, JSON_PRETTY_PRINT);

?>
