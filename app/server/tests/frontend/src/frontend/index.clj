(ns frontend.index
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
	(:require [frontend.common :as common])
;	(:require [frontend.filter] :as filter)
)

(def TITLE "Call List")

(def DEFAULT-LIMIT "10")
(def DEFAULT-OFFSET "0")

(defn set-default [default value]
	(if (nil? value) default value)
)

(defn line1 [call]
	[:div (get call "short_description")]
)

(defn line2 [call]
	[:div
		[:div.item (get call "call_number")]
		[:div.item (get call "division")]
		[:div.item (get call "type")]
		[:div.item (get call "commodity")]
		[:div.item (get call "commodity_type")]
	]
)

(defn line3 [call]
	[:div
		[:div.item (get (first (get call "buyers")) "buyer")]
		[:div.item (get call "posting_date")]
		[:div.item (get call "closing_date")]
	]
)

(defn call-lines [call]
	[:div.call 
		(line1 call) 
		(line2 call)
		(line3 call)
	]
)

(defn call-display [call]
	(let 
		[
			detail-button (form/submit-button "Details")
			details (list (form/hidden-field "document_id" (get call "document_id")) detail-button)
			detail-form [:div (form/form-to [:get "details.html"] details)]
		]
		(list (call-lines call) detail-form)
	)
)

(defn show [v]
	(println (nil? v) (empty? v) v)
)

(defn filter-drop [value]
	(not (or (= common/ALL value) (empty? value)))
)

(defn filter-date [value]
	(not (empty? value))
)

(defn filter-limit [value]
	true
)

(def FILTER-FNS
	{
		"division" filter-drop
		"type" filter-drop
		"commodity" filter-drop
		"commodity_type" filter-drop
		"buyer" filter-drop
		"posting_date_before" filter-date
		"posting_date_after" filter-date
		"closing_date_before" filter-date
		"closing_date_after" filter-date
		"limit" filter-limit
		"offset" filter-limit
	}
)

(defn filter-query-params [pair]
	((get FILTER-FNS (first pair)) (second pair))
)

(defn make-query-params [division type commodity commodity_type buyer posting_date_before posting_date_after closing_date_before closing_date_after limit offset]
	{
		"division" division
		"type" type
		"commodity" commodity
		"commodity_type" commodity_type
		"buyer" buyer
		"posting_date_before" posting_date_before
		"posting_date_after" posting_date_after
		"closing_date_before" closing_date_before
		"closing_date_after" closing_date_after
		"limit" (str limit)
		"offset" (str offset)
	}
)

(defn contents [api-base query-params]
	(let
		[
			filtered-params (filter filter-query-params query-params) 
			foo (println "Getting data")
			response (client/get (str api-base "documents") {:query-params filtered-params :accept :json})
			bar (println "Got data")
			body (get response :body)

			document-array (json/read-str body)
		]
		[:div (map call-display document-array)]
	)
)

(defn parse-limit [name argument]
	(try
		(when (some? argument) (Integer/parseInt argument))
		(catch NumberFormatException exception 
			(throw (Exception. (str "The " name " argument '" argument "' is not a number")))
		)
	)
)

(defn set-offset [up-down offset limit]
	(cond 
		(= up-down common/UP) (+ offset limit)
		(= up-down common/DOWN) (max 0 (- offset limit))
		:else offset
	)
)

(defn output [api-base local-base division type commodity commodity_type buyer 
								posting_date_before posting_date_after closing_date_before closing_date_after up-down limit-arg offset-arg]
	(try
		(let
			[
				baz (println "getting args")
				limit (parse-limit "limit" (set-default DEFAULT-LIMIT limit-arg))
				offset (parse-limit "offset" (set-default DEFAULT-OFFSET offset-arg))
				foo (println "making params")
				query-params 	(make-query-params division type commodity commodity_type buyer posting_date_before posting_date_after closing_date_before closing_date_after limit 
												(set-offset up-down offset limit)
											)
				bar (println "made params")
			]
			(common/page api-base local-base TITLE (contents api-base query-params) query-params)
		)
		(catch Exception error
			(page/html5 (list (common/head local-base TITLE) [:body error]))
		)
	)
)

(defn reset [api-base local-base]
	(let
		[
			query-params (make-query-params common/ALL common/ALL common/ALL common/ALL common/ALL "" "" "" "" nil nil)
		]
		(common/page api-base local-base TITLE (contents api-base query-params) query-params)
	)
)


