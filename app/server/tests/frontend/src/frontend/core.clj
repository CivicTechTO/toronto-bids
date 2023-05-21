(ns frontend.core
	(:gen-class)
	(:require [ring.adapter.jetty :as ring])
	(:require [ring.middleware.params :as params])
	(:require [ring.middleware.resource :refer [wrap-resource]])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as route])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [frontend.calls :as calls])
	(:require [frontend.common :as common])
	(:require [frontend.details :as details])
;	(:require [ring-debug-logging.core :as debug])
)

(compojure/defroutes toronto-bids
	(compojure/GET "/" [api-base] (calls/output api-base))

	(compojure/GET "*/calls.html"
		[
			api-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		] 
		(calls/output
			api-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		)
	)

	(compojure/GET "*/details.html"
		[
			api-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset call_number
		] 
		(details/output
			api-base call_number division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		)
	)

	(compojure/GET "*/call.html"
		[
			api-base call_number
		] 
		(details/output api-base call_number)
	)

	(route/not-found (page/html5 [:body [:div "Page not found"]]))
)

(defn make-wrap-argument [argument value]
	(fn [handler]
		(fn [request]
			(let [params (get request :params)]
				(handler (assoc request :params (assoc params argument value)))
			)
		)
	)
)

(defn make-bases-handler [api-base] 
	(let 
		[
			wrap-api-base (make-wrap-argument :api-base api-base)
		]
		(-> toronto-bids
;			(debug/wrap-with-logger)
			(wrap-api-base)
			(wrap-resource "public")
			(params/wrap-params)
		)
	)
)

(defn -main
  "toronto-bids test front end"
  [& args]
  	(if (== 2 (count args))
		(let 
			[
				api-base (first args)
				portString (first (rest args))
			]
			(try
				(let [port (Integer/parseInt portString)]
					(ring/run-jetty (make-bases-handler api-base) {:port port})
				)
				(catch NumberFormatException exception 
					(println (str portString " is not an int"))
				)
			)
		)  	
		(println "frontend api-base port")
	)
)
