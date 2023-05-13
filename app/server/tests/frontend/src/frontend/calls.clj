(ns frontend.calls
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [hiccup.util :as util])
	(:require [clj-http.client :as client])
	(:require [frontend.common :as common])
	(:require [frontend.selection :as selection])
;	(:require [frontend.filter] :as filter)
)

(def TITLE "Toronto Bids Archives")

(def DEFAULT-LIMIT "8")
(def DEFAULT-OFFSET "0")

(defn set-default [default value]
	(if (nil? value) default value)
)

(defn format-date-time [date-time]
	(first (str/split date-time #"T" 2))
)

(defn line1 [call]
	[:div.datecall
		[:div "<b>Posted:</b> " (format-date-time (get call "posting_date"))]
		[:div "<b>Closed:</b> " (format-date-time (get call "closing_date"))]
		[:div.callno (get call "call_number")]
	]
)

(defn line2 [call]
	[:div
		[:div.itemcom  (get call "commodity")]
		[:div.itemcom  (get call "commodity_type")]
		[:div.itemcat  (get call "division")]
		[:div.itemdesc (get call "short_description")]
		[:div.itemtype (get call "type")]
;		[:div.item (get (first (get call "buyers")) "buyer")]
	]
)

(defn call-lines [call]
	[:div.call 
		(line1 call) 
		(line2 call)
	]
)

(defn call-display [call]
	[:a.calllink
		{:href (util/url "/details.html" {:document_id (get call "document_id")})}
		(call-lines call)
	]
)

(defn show [v]
	(println (nil? v) (empty? v) v)
)

(defn filter-drop [value]
	(not (or (= selection/ALL value) (empty? value)))
)

(defn filter-empty [value]
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
		"posting_date_before" filter-empty
		"posting_date_after" filter-empty
		"closing_date_before" filter-empty
		"closing_date_after" filter-empty
		"search_text" filter-empty
		"limit" filter-limit
		"offset" filter-limit
	}
)

(defn filter-query-params [pair]
	((get FILTER-FNS (first pair)) (second pair))
)

(defn convert [value]
	(cond 
		(nil? value) ""
		:else value
	)
)

(defn make-query-params 
				[division type commodity commodity_type buyer posting_date_before posting_date_after closing_date_before closing_date_after search_text limit offset]
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
		"search_text" (convert search_text)
		"limit" (str limit)
		"offset" (str offset)
	}
)

(defn contents [api-base query-params]
	(let
		[
			filtered-params (filter filter-query-params query-params) 
			response (client/get (str api-base "documents") {:query-params filtered-params :accept :json})
			body (get response :body)

			document-array (json/read-str body)
			offset (Integer/parseInt (get query-params "offset"))
			limit (Integer/parseInt (get query-params "limit"))
		]
		(list
			[:div (map call-display document-array)]
			[:div
				[:a.back {:href (util/url "call_list.html" (assoc query-params "offset" (common/back limit offset)))} "< Prev results"]
				[:a.forward {:href (util/url "call_list.html" (assoc query-params "offset" (common/forward limit offset)))} "Next results >"]
			]
		)
	)
)

(defn list-body [api-base query-params]
	[:div#iframe-outer
		[:div (contents api-base query-params)]
	]
)

(defn list-page [api-base local-base query-params]
	(page/html5 (list (common/head local-base TITLE) [:body (list-body api-base query-params)]))
)

(defn main-body [api-base query-params]
	[:div#outer
		[:div#wrapper
			(selection/selection-form api-base query-params TITLE)
			(let 
				[
					url (util/url "call_list.html" query-params)
				]
				[:iframe#contents {:src url}]
			)
		]
	]
)

(defn main-page [api-base local-base query-params]
	(let 
	[
		main (main-body api-base query-params)
	]
		(page/html5 (list (common/head local-base TITLE) [:body main]))
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

(defn call-list [api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
								closing_date_before closing_date_after search_text limit-arg offset-arg]
	(try
		(let
			[
				limit (parse-limit "limit" (set-default DEFAULT-LIMIT limit-arg))
				offset (parse-limit "offset" (set-default DEFAULT-OFFSET offset-arg))
				query-params 	(make-query-params division type commodity commodity_type buyer posting_date_before posting_date_after 
												closing_date_before closing_date_after search_text limit offset
											)
			]
			(list-page api-base local-base query-params)
		)
		(catch Exception error
			(page/html5 (list (common/head local-base TITLE) [:body error]))
		)
	)
)

(defn reset [api-base local-base]
	(let
		[
			limit (parse-limit "limit" DEFAULT-LIMIT)
			offset (parse-limit "offset" DEFAULT-OFFSET)
			query-params (make-query-params selection/ALL selection/ALL selection/ALL selection/ALL selection/ALL "" "" "" "" "" limit offset)
		]
		(main-page api-base local-base query-params)
	)
)

(defn output [api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
								closing_date_before closing_date_after search_text limit-arg offset-arg direction]
	(try
		(let
			[
				limit (parse-limit "limit" (set-default DEFAULT-LIMIT limit-arg))
				offset-int (parse-limit "offset" (set-default DEFAULT-OFFSET offset-arg))
				offset (direction limit offset-int)
				query-params 	(make-query-params division type commodity commodity_type buyer posting_date_before posting_date_after 
												closing_date_before closing_date_after search_text limit offset
											)
			]
			(main-page api-base local-base query-params)
		)
		(catch Exception error
			(page/html5 (list (common/head local-base TITLE) [:body error]))
		)
	)
)


