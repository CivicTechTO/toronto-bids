<!DOCTYPE html>
<html lang="en">
<head>

  <!-- Basic Page Needs
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <meta charset="utf-8">
  <title>Toronto Bids Archive</title>
  <meta name="description" content="">
  <meta name="author" content="">

  <!-- Mobile Specific Metas
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <!-- FONT
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <link href="//fonts.googleapis.com/css?family=Raleway:400,300,600" rel="stylesheet" type="text/css">

  <!-- CSS
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <link rel="stylesheet" href="css/normalize.css">
  <link rel="stylesheet" href="css/skeleton.css">
  <link rel="stylesheet" href="css/app.css">

  <!-- Favicon
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <link rel="icon" type="image/png" href="images/favicon.png">


<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-GXWPTRZ3GT"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', 'G-GXWPTRZ3GT');
</script>

</head>
<body>

<?php
$urlbase = "https://torontobidsstorage.file.core.windows.net/torontobids/ariba_data/";
$ariba_sas_token = "?sv=2022-11-02&ss=f&srt=sco&sp=rl&se=2123-05-18T04:49:53Z&st=2023-05-17T20:49:53Z&spr=https,http&sig=uWfbBiXayfnnSxN%2FpmRW%2FBOtVOyGcY%2F%2Fcz5lN8gjAP4%3D";

$haverecord=0;
$url = "http://pwd.ca/torontobidsarchive/api.php?cn=".urldecode($_REQUEST['cn']);
$json = file_get_contents($url);
$json = json_decode($json);
if (empty($json->data)) {
	print "no record found";
} else {
	$haverecord=1;
	#var_dump($json->data);
}
?>

  <!-- Primary Page Layout
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <div class="container">
    <div class="row recordheader">
      <div class="twelve columns" style="margin-top: 10%">
        <h1><a href="http://pwd.ca/torontobidsarchive/">Open Bids Toronto</a></h1>
        <p>A searchable archive of past City of Toronto bids and tenders.</p>
      </div>
    </div>
    <div class="recorddisplay">
<?php
foreach($json->data as $key => $value) {
	if ( ($key != "parsedtext") && ($key != "urls") && ($key != "BuyerPhoneShow") && ($key != "BuyerEmailShow") && ($key != "BuyerLocationShow") && ($key != "lastupdated") && ($key != "uuid") && ($key != "attachments") ) {
?>
    <div class="row">
      <div class="three columns"><?=$key?></div>
      <div class="nine columns"><?=$value?></div>      
	</div>
<?
	} elseif ($key == "parsedtext") {
		$parsedtext = $value;
	} elseif ($key == "attachments") {
?>
    <div class="row">
      <div class="twelve columns attachments-section"><strong>Attachments:</strong><br/><ul>
<?php
foreach($value as $item) {
?>	<li><a href="<?=$urlbase?><?=$_REQUEST['cn']?>/<?=$item?><?=$ariba_sas_token?>"><?=$item?></a><?
}
?>
      </ul></div>
	</div>
<?	
	}
}
?>
    <div class="row">
      <div class="twelve columns"><strong>Text from attachments:</strong><br/>
      <pre><?=$parsedtext?></pre></div>
	</div>

    </div>
    <div class="footer-section">
      <div class="row">
        <div class="twelve columns">
        <a href="">About</a>
        </div>
      </div>
    </div>
  </div>
<!-- End Document
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
</body>
</html>
