(ns frontend.common
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
	(:require [clj-http.client :as client])
)

(def ALL "*All*")

(defn filter-line [label filter-fn]
	[:div 
		(list 
			[:div.label label]
			(filter-fn)
		)
	]
)

(defn select [api-base label name request selected]
	(let
		[
			response (client/get (str api-base request) {:accept :json})
			body (get response :body)

			pick-list (json/read-str body)

			filter-fn (fn [] (form/drop-down name (cons ALL pick-list) selected))
		]
		(filter-line label filter-fn)
	)
)

(defn date [label name value]
	(let 
		[
			filter-fn (fn [] [:input {:type "date" :name name :value value}])
		]
		(filter-line label filter-fn)
	)
)

(defn selection-form [api-base query-params]
	[:div#select
		(list
			[:div
				(form/form-to [:get "index.html"]
					(select api-base "Division" "division" "plain_divisions" (get query-params "division"))
					(select api-base "Type" "type" "plain_types" (get query-params "type"))
					(select api-base "Commodity" "commodity" "plain_commodities" (get query-params "commodity"))
					(select api-base "Commodity type" "commodity_type" "plain_commodity_types" (get query-params "commodity_type"))
					(select api-base "Buyer" "buyer" "plain_buyers" (get query-params "buyer"))
					(date "Posted on or before", "before_post_date", (get query-params "before_post_date"))
					(date "Posted on or after", "after_post_date", (get query-params "after_post_date"))
					(date "Closed on or before", "before_close_date", (get query-params "before_close_date"))
					(date "Closed on or after", "after_close_date", (get query-params "after_close_date"))
					(form/hidden-field "limit" (get query-params "limit"))
					(form/hidden-field "offset" (get query-params "offset"))
					[:div (form/submit-button "Reload")]
				) 

				(form/form-to [:get "reset.html"] (form/submit-button "Reset"))

				(form/form-to [:get "next.html"] 
					(form/hidden-field "limit" (get query-params "limit"))
					(form/hidden-field "offset" (get query-params "offset"))
					(form/submit-button "Next")
				)

				(form/form-to [:get "previous.html"] 
					(form/hidden-field "limit" (get query-params "limit"))
					(form/hidden-field "offset" (get query-params "offset"))
					(form/submit-button "Previous")
				)
			]
		)
	]
)

(defn body [title api-base contents query-params]
	[:div#outer
		[:div#title title]
		[:div#wrapper
			(selection-form api-base query-params)
			[:div#contents
				contents
			]
		]
	]
)


(defn head [local-base title]
	[:head
		[:title title]
		(page/include-css (str local-base "styles.css"))
	]
)

(defn page [api-base local-base title contents query-params]
	(page/html5 (list (head local-base title) [:body (body title api-base contents query-params)]))
)
