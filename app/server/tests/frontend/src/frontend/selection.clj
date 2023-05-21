(ns frontend.selection
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
	(:require [frontend.common :as common])
)

(defn create-line [label create-fn]
	[:div 
		(list 
			[:div.label label]
			(create-fn)
		)
	]
)

(defn select [api-base label name request selected]
	(let
		[
			pick-list (common/api-call api-base request {})
			create-fn (fn [] (form/drop-down name (cons common/ALL pick-list) selected))
		]
		(create-line label create-fn)
	)
)

(defn date [label name value]
	(let 
		[
			create-fn (fn [] [:input {:type "date" :name name :value value}])
		]
		(create-line label create-fn)
	)
)

(defn text [label name value]
	(let 
		[
			create-fn (fn [] [:input.search {:type "text-area" :name name :value value}])
		]
		(create-line label create-fn)
	)
)

(defn button-box [name action label]
	[:div.button (form/submit-button {:formaction action} label)]
)

(defn selection-form [api-base query-params title]
	[:div#select
		[:div#title [:a {:href "/"} title]]
		(list
			[:div
				(form/form-to [:get "calls.html"]
					(text "Search for", "search_text", (get query-params "search_text"))
					[:input#expand-toggle {:type "checkbox"}]
					[:label#expand-filters {:for "expand-toggle"} "Show filters"]
					[:div#filters
						(select api-base "Division" "division" "plain_divisions.json" (get query-params "division"))
;						(select api-base "Type" "type" "plain_types.json" (get query-params "type"))
						(select api-base "Commodity" "commodity" "plain_commodities.json" (get query-params "commodity"))
						(select api-base "Commodity type" "commodity_type" "plain_commodity_types.json" (get query-params "commodity_type"))
;						(select api-base "Buyer" "buyer" "plain_buyers.json" (get query-params "buyer"))
						(date "Posted on or before", "posting_date_before", (get query-params "posting_date_before"))
						(date "Posted on or after", "posting_date_after", (get query-params "posting_date_after"))
						(date "Closed on or before", "closing_date_before", (get query-params "closing_date_before"))
						(date "Closed on or after", "closing_date_after", (get query-params "closing_date_after"))
						(form/hidden-field "limit" (get query-params "limit"))
						(form/hidden-field "offset" 0)

						(button-box "reload" "calls.html" "Apply filters")
						[:a {:href "/" :style "display: inline"} "Reset filters"]
					] 
				) 
			]
		)
	]
)

