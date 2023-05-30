(ns frontend.core
	(:gen-class)
	(:require [ring.adapter.jetty :as ring])
	(:require [ring.middleware.params :as params])
	(:require [clojure.java.io :as io])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as route])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [frontend.calls :as calls])
	(:require [frontend.common :as common])
	(:require [frontend.details :as details])
;	(:require [ring-debug-logging.core :as debug])
)

(def css-file (io/resource "public/styles.css"))

(defn css-response
  "The HTTP response for the CSS file."
  []
	{
		:status 200
		:headers {"Content-Type" "text/css"}
		:body (slurp css-file)
	}
)

(compojure/defroutes toronto-bids
	(compojure/GET "*/" [api-base local-base] (calls/output api-base local-base))

	(compojure/GET "*/calls.html"
		[
			api-base local-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		] 
		(calls/output
			api-base local-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		)
	)

	(compojure/GET "*/details.html"
		[
			api-base local-base division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset call_number
		] 
		(details/output
			api-base local-base call_number division type commodity commodity_type buyer
			posting_date_before posting_date_after closing_date_before closing_date_after
			search_text limit offset
		)
	)

	(compojure/GET "*/call.html"
		[
			api-base local-base call_number
		] 
		(details/output api-base local-base call_number)
	)

	(compojure/GET "*/styles.css" [] (css-response))

	(route/not-found (page/html5 [:body [:div "Page not found"]]))
)

(defn make-wrap-argument
  "Creates a wrapper that adds the given key-value pair to the request's params map."
  [argument value]
	(fn [handler]
		(fn [request]
			(let [params (get request :params)]
				(handler (assoc request :params (assoc params argument value)))
			)
		)
	)
)

(defn make-bases-handler
  "Add wrappers to toronto-bids for providing api-base, local-base, and URL-encoded parameters."
  [api-base local-base]
	(let 
		[
			wrap-api-base (make-wrap-argument :api-base api-base)
			wrap-local-base (make-wrap-argument :local-base local-base)
		]
		(-> toronto-bids
;			(debug/wrap-with-logger)
			(wrap-api-base)
			(wrap-local-base)
			(params/wrap-params)
		)
	)
)

(defn -main
  "toronto-bids test front end"
  [& args]
	(if (== 3 (count args))
		(let 
			[
				api-base (first args)
				portString (first (rest args))
                                local-base (first (rest (rest args)))
			]
			(try
				(let [port (Integer/parseInt portString)]
					(ring/run-jetty (make-bases-handler api-base local-base) {:port port})
				)
				(catch NumberFormatException exception 
					(println (str portString " is not an int"))
				)
			)
		)  	
		(println "frontend api-base port local-base")
	)
)
