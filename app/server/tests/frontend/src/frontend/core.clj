(ns frontend.core
	(:gen-class)

;	(:require [clojure.string :as str])
	(:require [ring.adapter.jetty :as ring])
	(:require [ring.middleware.params :as params])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as route])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
  (:require [clojure.java.io :as io])
	(:require [frontend.index :as index])
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

(compojure/defroutes toronto-bids
	(compojure/GET "*/index.html" 
		[api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit offset] 
		(index/output api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit offset)
	)
	(compojure/GET "*/reset.html" [api-base local-base] (index/reset[api-base local-base]))
	(compojure/GET "*/next.html" 
		[api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit offset] 
		(index/next api-base local-base division type commodity commodity_type buyer before_post_date after_post_date before_close_date after_close_date limit offset)
	)

	(compojure/GET "*/details.html" [api-base local-base document_id] (details/output api-base local-base document_id))
	(compojure/GET "*/attachments.html" [api-base local-base document_id] (attachments/output api-base local-base document_id))


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
