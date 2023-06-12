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
$url = "http://pwd.ca/torontobidsarchive/api.php?l=4";
$json = file_get_contents($url);
$json = json_decode($json);
  
if (empty($json->data)) {
	print "no record found";
} else {
	$haveresults = 1;
}

$rhaveresults = 0;
$rurl = "http://pwd.ca/torontobidsarchive/api.php?l=4&sort=random";
$rjson = file_get_contents($rurl);
$rjson = json_decode($rjson);
  
if (empty($rjson->data)) {
	print "Rno record found";
} else {
	$rhaveresults = 1;
}
?>


  <!-- Primary Page Layout
  –––––––––––––––––––––––––––––––––––––––––––––––––– -->
  <div class="container">
    <div class="row">
      <div class="twelve columns" style="margin-top: 10%">
        <h1>Toronto Bids Archive</h1>
        <p>A searchable archive of past City of Toronto bids and tenders.</p>
      </div>
    </div>
    <form action="results.php" method="get">
      <div class="row">
        <div class="eight columns">
          <input class="u-full-width" type="search" placeholder="Search" id="search" name="q">
        </div>
        <div class="four columns">
          <input class="button-primary u-full-width" type="submit" value="Search">
        </div>
    </form>
  </div>
  <div class="info-section">
    <div class="row">
      <div class="six columns">
        <h5>Recently closed bids</h5>
        <table class="u-full-width">
          <thead><tr><th></th></tr></thead>
          <tbody>
<?php
if ($haveresults) {
	foreach($json->data as $key) {
?>            <tr><td><a href='<?=$key->{"CallNumber"}?>.html'><?=$key->{"ShortDescription"}?></a> <?=$key->{"ClosingDate"}?><?php
if ($key->{'textlength'} > 10) {
?> &#128452;<?
}
?></td></tr><?
	}
}
?>
<tr><td style="text-align: right;"><a href="results.php">[more]</a></td></tr>
          </tbody>
        </table>
      </div>
      <div class="six columns">
        <h5>Explore</h5>
        <table class="u-full-width">
          <thead><tr><th></th></tr></thead>
          <tbody>
<?php
if ($rhaveresults) {
	foreach($rjson->data as $key) {
?>            <tr><td><a href='<?=$key->{"CallNumber"}?>.html'><?=$key->{"ShortDescription"}?></a> <?=$key->{"ClosingDate"}?><?php
if ($key->{'textlength'} > 10) {
?> &#128452;<?
}
?></td></tr><?
	}
}
?>
          </tbody>
        </table>
      </div>
    </div>
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
