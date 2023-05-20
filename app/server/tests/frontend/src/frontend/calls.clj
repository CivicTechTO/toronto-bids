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

(defn format-date-time [date-time]
	(first (str/split date-time #"T" 2))
)

(defn line1 [call]
	[:div.datecall
;		[:div "<b>Posted:</b> " (format-date-time (get call "posting_date"))]
		[:div.date (format-date-time (get call "closing_date"))]
		[:div.callno (get call "call_number")]
	]
)

(defn line2 [call]
	[:div
		[:div.itemcom  (get call "commodity")]
		[:div.itemcom  (get call "commodity_type")]
		[:div.itemcat  (get call "division")]
		[:div.itemdesc (get call "short_description")]
;		[:div.itemtype (get call "type")]
;		[:div.item (get (first (get call "buyers")) "buyer")]
	]
)

(defn call-lines [call]
	[:div.call 
		(line1 call) 
		(line2 call)
	]
)

(defn call-display [query-params call]
	[:a.calllink
		{:href (util/url "/details.html" (assoc query-params :document_id (get call "document_id")))}
		(call-lines call)
	]
)

(defn show [v]
	(println (nil? v) (empty? v) v)
)

(defn filter-drop [value]
	(not (or (= common/ALL value) (empty? value)))
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

(defn contents [api-base query-params]
	(let
		[
			filtered-params (into {} (filter filter-query-params query-params))
			document-array (common/api-call api-base "documents.json" filtered-params)
			offset (Integer/parseInt (get query-params "offset"))
			limit (Integer/parseInt (get query-params "limit"))
		]
		(list
			[:div (map (partial call-display query-params) document-array)]
			[:div
				[:a.back {:href (util/url "calls.html" (assoc query-params "offset" (common/back limit offset)))} "< Prev results"]
				[:a.forward {:href (util/url "calls.html" (assoc query-params "offset" (common/forward limit offset)))} "Next results >"]
			]
		)
	)
)

(defn list-body [api-base query-params]
	[:div#content
		(contents api-base query-params)
	]
)

(defn main-body [api-base query-params]
	[:div#wrapper
		(selection/selection-form api-base query-params common/title)
		(list-body api-base query-params)
	]
)

(defn main-page [api-base query-params]
	(let 
	[
		main (main-body api-base query-params)
	]
		(page/html5 (list (common/head common/title "calls.css") [:body main]))
	)
)

(defn reset [api-base]
	(main-page api-base common/default-query-params)
)

(defn output [api-base division type commodity commodity_type buyer posting_date_before posting_date_after 
								closing_date_before closing_date_after search_text limit-arg offset-arg direction]
	(try
		(let
			[
				limit (common/parse-limit "limit" (common/set-default common/DEFAULT-LIMIT limit-arg))
				offset-int (common/parse-limit "offset" (common/set-default common/DEFAULT-OFFSET offset-arg))
				offset (direction limit offset-int)
				query-params 	(common/make-query-params division type commodity commodity_type buyer posting_date_before posting_date_after 
												closing_date_before closing_date_after search_text limit offset
											)
			]
			(main-page api-base query-params)
		)
		(catch Exception error
			(page/html5 (list (common/head common/title "calls.css") [:body error]))
		)
	)
)

