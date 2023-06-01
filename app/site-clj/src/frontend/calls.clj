(ns frontend.calls
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [hiccup.page :as page])
	(:require [hiccup.util :as util])
	(:require [frontend.common :as common])
	(:require [frontend.selection :as selection])
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
	]
)

(defn call-lines [call]
	[:div.call 
		(line1 call) 
		(line2 call)
	]
)

(defn call-display
  "Displays an info panel for the given call, linked to the call details page."
  [local-base query-params call]
	[:a.calllink
		{:href (util/url (str local-base "/details.html") (assoc query-params :call_number (get call "call_number")))}
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
  "Specifies when to keep the given filter categories for the API call."
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

(defn filter-query-params
  "Applies the pair's key's FILTER-FN to its value."
  [pair]
	((get FILTER-FNS (first pair)) (second pair))
)

(defn contents
  "Fetches calls and displays the results, with forward and back links at the bottom."
  [api-base local-base query-params]
	(let
		[
			filtered-params (into {} (filter filter-query-params query-params))
			document-array (common/api-call api-base "documents.json" filtered-params)
			offset (Integer/parseInt (get query-params "offset"))
			limit (Integer/parseInt (get query-params "limit"))
		]
		(list
			[:div (map (partial call-display local-base query-params) document-array)]
			[:div
				[:a.back {:href (util/url (str local-base "/calls.html") (assoc query-params "offset" (common/back limit offset)))} "< Prev results"]
				[:a.forward {:href (util/url (str local-base "/calls.html") (assoc query-params "offset" (common/forward limit offset)))} "Next results >"]
			]
		)
	)
)

(defn list-body [api-base local-base query-params]
	[:div#content
		(contents api-base local-base query-params)
	]
)

(defn main-body [api-base local-base query-params]
	[:div#wrapper
		(selection/selection-form api-base local-base query-params common/title)
		(list-body api-base local-base query-params)
	]
)

(defn main-page [api-base local-base query-params]
	(page/html5 (common/head local-base) [:body (main-body api-base local-base query-params)])
)

(defn output
  "Generates HTML for the calls page given query parameters."
	([api-base local-base] (main-page api-base local-base common/default-query-params))
	([api-base local-base division type commodity commodity_type buyer
	  posting_date_before posting_date_after closing_date_before closing_date_after
	  search_text limit-arg offset-arg]
		(try
			(let
				[
					limit (common/parse-int "limit" limit-arg common/DEFAULT-LIMIT)
					offset (common/parse-int "offset" offset-arg common/DEFAULT-OFFSET)
					query-params (common/make-query-params
						division type commodity commodity_type buyer
						posting_date_before posting_date_after closing_date_before closing_date_after
						search_text limit offset
					)
				]
				(main-page api-base local-base query-params)
			)
			(catch Exception error
				(page/html5 (list (common/head local-base) [:body error]))
			)
		)
	)
)

