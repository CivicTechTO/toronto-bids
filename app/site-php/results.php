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
$haveresults = 0;
$querystring = "q=";
$querystring .= urlencode($_REQUEST['q']);
if (!empty($_REQUEST['d'])) $querystring .= "&d=".urlencode($_REQUEST['d']);
if (!empty($_REQUEST['c'])) $querystring .= "&c=".urlencode($_REQUEST['c']);
if (!empty($_REQUEST['ct'])) $querystring .= "&ct=".urlencode($_REQUEST['ct']);  
$url = "http://pwd.ca/torontobidsarchive/api.php?".$querystring;
$json = file_get_contents($url);
$json = json_decode($json);

//var_dump($json);
if (empty($json->data)) {
  //print "no record found";
} else {
  $haveresults = 1;
}
if (!empty($_REQUEST['q'])) {
	$search_term_to_display = urldecode($_REQUEST['q']);
} else {
	$search_term_to_display = "( none )";
}
?>
<!-- <?=var_dump($url);?>-->
<!-- Primary Page Layout
–––––––––––––––––––––––––––––––––––––––––––––––––– -->
<div class="container">
  <div class="row resultsheader">
    <div class="twelve columns" style="margin-top: 10%">
      <h1><a href="http://pwd.ca/torontobidsarchive/">Toronto Bids Archive</a></h1>
    </div>
  </div>
  <form action="results.php" method="get">
  <input type="hidden" name="q" value="<?=$_REQUEST['q']?>">
  <div class="row" style="font-weight: 600">
    <div class="four columns">
			Search term: <?=$search_term_to_display?>
    </div>
	<div class="four columns" style="text-align: center;">
			Total results: <?=$json->meta->{"total"}?>
	</div>
	<div class="four columns" style="text-align: right;">
			<a href="index.php">New search</a>
	</div>
<?php
if ($haveresults) {
?>
<div class="filtersection">
    <div class="row">
		<div class="twelve columns" style="font-weight: 600">
      <br/>Filter by:<br/>
		</div>
	</div>
    <div class="row">
		<div class="three columns">
			<label for="c">Commodity</label>
			<select class="u-full-width" name="c" id="c">
<?php
	if (count((array)$json->included->topCommodity) > 1) {
?>			<option value=""></option>
<?
	} 
	foreach($json->included->topCommodity as $item => $val) {
?>			<option value="<?=$item?>"><?=$item?> (<?=$val?>)</option>
<?
	}
?>
			</select>
		</div>
		<div class="three columns">
			<label for="ct">CommodityType</label>
			<select class="u-full-width" name="ct" id="ct">
<?php
	if (count((array)$json->included->topCommodityType) > 1) {
?>			<option value=""></option>
<?
	} 
	foreach($json->included->topCommodityType as $item => $val) {
?>			<option value="<?=$item?>"><?=$item?> (<?=$val?>)</option>
<?
	}
?>
			</select>
		</div>
		<div class="three columns">
			<label for="d">Division</label>
			<select class="u-full-width" name="d" id="d">
<?php
	if (count((array)$json->included->topDivision) > 1) {
?>			<option value=""></option>
<?
	} 
	foreach($json->included->topDivision as $item => $val) {
		if (empty($item)) {$item = "(UNSPECIFIED)"; $newitem = "null";} else {$newitem = $item;}
?>			<option value="<?=$newitem?>"><?=$item?> (<?=$val?>)</option>
<?
	}
?>
			</select>
		</div>
	    <div class="three columns" style=";">
          <br/><input class="button-primary u-full-width" type="submit" value="Filter">
		</div>
<?
}
?>
  </form>
</div>
<div class="results-section">
<?php
if ($haveresults) {
  foreach($json->data as $key) {
	switch ($key->{"Commodity"}) {
		case "Construction Services": $col = "comc"; break;
		case "Professional Services": $col = "comp"; break;
		default: $col = "comg"; #goods and services
	}
    ?>
    <div class='result'><a href='<?=$key->{"CallNumber"}?>.html'><div class='datecall'><?=$key->{"ClosingDate"}?></div>
    <div class="itemcom <?=$col?>"><?=$key->{"Commodity"}?></div><br/>
    <?=$key->{"ShortDescription"}?><div class="callno"><?=$key->{"CallNumber"}?></div><?php
if ($key->{'textlength'} > 10) {
?> &#128452;<?
}
?></a>
    </div><?php
  }
} else {
  ?><div class="row">No results</div><?
}
?>
</div>
  <div class="footer-section">
    <div class="row">
      <div class="twelve columns">
      <a href="/torontobidsarchive/">Home</a> | <a href="about.php">About</a>
      </div>
    </div>
  </div>
</div>
<!-- End Document
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
</body>
</html>