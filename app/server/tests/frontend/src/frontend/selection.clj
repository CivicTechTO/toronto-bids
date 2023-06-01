(ns frontend.selection
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
)

(def ALL "*All*")

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
			response (client/get (str api-base request) {:accept :json})
			body (get response :body)

			pick-list (json/read-str body)

			create-fn (fn [] (form/drop-down name (cons ALL pick-list) selected))
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
			create-fn (fn [] [:input {:type "text-area" :name name :value value}])
		]
		(create-line label create-fn)
	)
)

(defn button-box [name action label]
	[(keyword (str "div#name")) (form/submit-button {:formaction action} label)]
)

(defn selection-form [api-base query-params]
	[:div#select
		(list
			[:div
				(form/form-to [:get "calls.html"]
					(select api-base "Division" "division" "plain_divisions" (get query-params "division"))
					(select api-base "Type" "type" "plain_types" (get query-params "type"))
					(select api-base "Commodity" "commodity" "plain_commodities" (get query-params "commodity"))
					(select api-base "Commodity type" "commodity_type" "plain_commodity_types" (get query-params "commodity_type"))
					(select api-base "Buyer" "buyer" "plain_buyers" (get query-params "buyer"))
					(date "Posted on or before", "posting_date_before", (get query-params "posting_date_before"))
					(date "Posted on or after", "posting_date_after", (get query-params "posting_date_after"))
					(date "Closed on or before", "closing_date_before", (get query-params "closing_date_before"))
					(date "Closed on or after", "closing_date_after", (get query-params "closing_date_after"))
					(text "Search for", "search_text", (get query-params "search_text"))
					(form/hidden-field "limit" (get query-params "limit"))
					(form/hidden-field "offset" (get query-params "offset"))

					(button-box "forward" "forward.html" "Forward")
					(button-box "back" "back.html" "Back")

					(button-box "reload" "calls.html" "Reload")

					(button-box "reset" "reset.html" "Reset")
				) 
			]
		)
	]
)

