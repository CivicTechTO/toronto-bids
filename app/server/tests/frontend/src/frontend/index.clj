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

(def DEFAULT-LIMIT 10)
(def DEFAULT-OFFSET 0)

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
(println (get call "buyer"))
	[:div
		[:div.item (first (get call "buyer"))]
		[:div.item (get call "posting_date")]
		[:div.item (get call "closing_date")]
	]
)
(defn call-display [call]
	[:div.call 
		(line1 call) 
		(line2 call)
		(line3 call)
	]
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
		"before_post_date" filter-date
		"after_post_date" filter-date
		"before_close_date" filter-date
		"after_close_date" filter-date
		"limit" filter-limit
		"offset" filter-limit
		}
	)

(defn filter-query-params [pair]
	((get FILTER-FNS (first pair)) (second pair))
)

(defn make-query-params [division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg]
	(let
		[
			limit (set-default DEFAULT-LIMIT limit-arg)
			offset (set-default DEFAULT-OFFSET offset-arg)
		]
		{
			"division" division
			"type" type
			"commodity" commodity
			"commodity_type" commodity_type
			"buyer" buyer
			"before_post_date" before_post_date
			"after_post_date" after_post_date
			"before_close_date" before_close_date
			"after_close_date" after_close_date
			"limit" limit
			"offset" offset
		}
	)
)

(defn contents [api-base query-params]
	(let
		[
			filtered-params (filter filter-query-params query-params) 
			response (client/get (str api-base "documents") {:query-params filtered-params :accept :json})
			body (get response :body)

			document-array (json/read-str body)
		]
		[:div (map call-display document-array)]
	)
)

(defn parse-limit [name argument]
	(try
		(Integer/parseInt argument)
		(catch NumberFormatException exception 
			(throw (Exception. (str "The " name " argument '" argument "' is not a number")))
		)
	)
)

(defn output [api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg]
	(let
		[
			query-params (make-query-params division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg)
		]
		(common/page api-base local-base TITLE (contents api-base query-params) query-params)
	)
)

(defn change-page [direction api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg]
	(try
		(let
			[
				limit (parse-limit "limit" limit-arg)
				offset (parse-limit "offset" offset-arg)
				query-params 	(make-query-params division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit 
												(max 0 (direction offset limit))
											)
			]
			(common/page api-base local-base TITLE (contents api-base query-params))
		)
		(catch Exception error
			(page/html5 (list (common/head local-base TITLE) [:body error]))
		)
	)
)

(defn next-page [api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg]
	(change-page + api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg)
)

(defn previous-page [api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg]
	(change-page - api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit-arg offset-arg)
)

(defn reset [api-base local-base]
	(let
		[
			query-params (make-query-params common/ALL common/ALL common/ALL common/ALL common/ALL "" "" "" "" nil nil)
		]
		(common/page api-base local-base TITLE (contents api-base query-params) query-params)
	)
)


