<?php

$safe_string_to_store = "------- ".date("Y-m-d H:i:s")." -------\n". base64_encode(serialize($_REQUEST))."\n".print_r($_REQUEST,TRUE)."\n";

$filename = 'input.log';
if (is_writable($filename)) {
	if (!$fp = fopen($filename, 'a')) {
		echo "Cannot open file ($filename)";
		exit;
	}
	if (fwrite($fp, $safe_string_to_store) === FALSE) {
		echo "Cannot write to file ($filename)";
		exit;
	}
	fclose($fp);

} else {
    echo "The file $filename is not writable";
}
?>