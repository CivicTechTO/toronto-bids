(ns frontend.core
	(:gen-class)

	(:require [ring.adapter.jetty :as ring])
	(:require [ring.middleware.params :as params])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as route])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [clojure.java.io :as io])
	(:require [frontend.calls :as calls])
	(:require [frontend.details :as details])
	(:require [frontend.attachments :as attachments])
;	(:require [ring-debug-logging.core :as debug])
)

(def CSS (io/resource "public/styles.css"))

(defn css []
	{
		:status 200
		:headers {"Content-Type" "text/css"}
		:body (slurp CSS)
	}
)

(defn stay [limit offset]
	offset
)

(defn forward [limit offset]
	(+ offset limit)
)

(defn back [limit offset]
	(max 0 (- offset limit))
)

(compojure/defroutes toronto-bids
	(compojure/GET "*/calls.html" 
		[
			api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
			closing_date_before closing_date_after search_text limit offset
		] 
		(calls/output api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
									closing_date_before closing_date_after search_text limit offset stay
		)
	)

	(compojure/GET "*/forward.html" 
		[
			api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
			closing_date_before closing_date_after search_text limit offset
		] 
		(calls/output api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
									closing_date_before closing_date_after search_text limit offset forward
		)
	)

	(compojure/GET "*/back.html" 
		[
			api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
			closing_date_before closing_date_after search_text limit offset
		] 
		(calls/output api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
									closing_date_before closing_date_after search_text limit offset back
		)
	)

	(compojure/GET "*/reset.html" [api-base local-base] (calls/reset api-base local-base))

	(compojure/GET "*/call_list.html" 
		[
			api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
			closing_date_before closing_date_after search_text limit offset
		] 
		(calls/call-list api-base local-base division type commodity commodity_type buyer posting_date_before posting_date_after 
									closing_date_before closing_date_after search_text limit offset
		)
	)

	(compojure/GET "*/details.html" [api-base local-base document_id] (details/output api-base local-base document_id))

;	(compojure/GET "*/attachments.html" [api-base local-base document_id] (attachments/output api-base local-base document_id))

	(compojure/GET "*/styles.css" [] (css))

;	(route/resources "/")

	(route/not-found (hiccup/html (page/html5 [:body [:div "Page not found"]])))
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

(defn make-bases-handler [api-base local-base] 
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
