<?php
/*
Next steps:
- Insert each record into a mysql database?
	- in such a way that identical data is untouched, modified data is overwritten (or better, versioned!?)
- Make a way to view the data...? (phpmysql in the meantime)



*/

$myXMLData = file_get_contents('https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/645e682e-5504-46fe-aaa4-cb27e6384381/resource/2160e5d4-6c6c-4895-95ce-9831b3553dc6/download/Construction%20Services,%20Goods%20&%20Services,%20and%20Professional%20Services.xml');
$xml=simplexml_load_string($myXMLData) or die("Error: Cannot create object");

foreach($xml->viewentry as $entryobj) {
	foreach($entryobj->entrydata as $gabe) {
		if (trim($gabe['name']) != "$12") 
			$data[trim($entryobj['position'])][trim($gabe['name'])] = trim($gabe->text);
		if (trim($gabe['name']) == "AllAttachments") {
			$doc = new DOMDocument();
  			$doc->loadHTML(trim($gabe->text));
  			$urls = array();
  			$links = $doc->getElementsByTagName('a');
  			foreach ($links as $link)
  				$urls[] = $link->getAttribute('href');
  			if (!empty($urls)) {
  				$data[trim($entryobj['position'])]['urls'] = $urls;
  			}
  		}
	}
}

print "\n\n";
$count = 0;
foreach ($data as $r) {
	if (empty($r['urls']))  $r['urls'] = []; 
	if (empty($r['CallNumber'])) $r['CallNumber'] = uniqid('blank_');
	print "INSERT INTO fromxml (Commodity,CommodityType,CallNumber,Type,ShortDescription,Description,ShowDatePosted,ClosingDate,SiteMeeting,ShowBuyerNameList,BuyerPhoneShow,BuyerEmailShow,Division,BuyerLocationShow,urls,uuid) ".
		"VALUES('".$r['Commodity']."','".$r['CommodityType']."','".$r['CallNumber']."','".$r['Type']."','".addslashes($r['ShortDescription'])."','".addslashes($r['Description'])."','".$r['ShowDatePosted']."','".$r['$4']."','".$r['SiteMeeting']."','".$r['ShowBuyerNameList']."','".$r['BuyerPhoneShow']."','".$r['BuyerEmailShow']."','".addslashes($r['Division'])."','".addslashes($r['BuyerLocationShow'])."','".implode(",",$r['urls'])."',UUID());\n";
	$count++;
}
print "\n\nCount: ".$count."\n";
?>
