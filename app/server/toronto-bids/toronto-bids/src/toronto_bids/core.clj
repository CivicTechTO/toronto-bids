(ns toronto-bids.core
  (:gen-class)
	 (:require [clojure.string :as str])
	 (:require [ring.adapter.jetty :as ring])
	 (:require [ring.middleware.params :as params])
	 (:require [compojure.core :as compojure])
	 (:require [compojure.route :as compojure-route])
	 (:require [clojure.java.jdbc :as jdbc])
	 (:require [clojure.data.json :as json])
	 (:require [toronto-bids.stuff :as stuff])
	 (:require [toronto-bids.documents :as documents])
;	 (:require [ring-debug-logging.core :as debug])
)

(defn write-simple [db table columns]
		(let 
			 [
				  query (str "SELECT " columns " FROM " table)
			 ]
			 (json/write-str (jdbc/query db [query]))
		)
)

(compojure/defroutes toronto-bids
		(compojure/GET "*/api/types" [db] (write-simple db "type" "id, type"))
		(compojure/GET "*/api/commodities" [db] (write-simple db "commodity" "id, commodity"))
		(compojure/GET "*/api/commodity_types" [db] (write-simple db "commodity_type" "id, commodity_id, commodity_type"))
		(compojure/GET "*/api/divisions" [db] (write-simple db "division" "id,division"))
		(compojure/GET "*/api/buyers" [db] (write-simple db "buyer" "id, buyer"))

		(compojure/GET "*/api/documents" 
			[db call-number type division commodity commodity-type posting-date-after posting-date-before closing-date-after closing-date-before buyer search-text limit offset] 
			(documents/write-documents db 
				(list call-number type division commodity commodity-type posting-date-after posting-date-before closing-date-after closing-date-before buyer search-text) 
				limit offset)
			)

		(compojure-route/not-found "Not found")
)

(defn make-wrap-db [db]
	(fn [handler]
		(fn [request]
			(let [params (get request :params)]
				(handler (assoc request :params (assoc params :db db)))
			)
		)
	)
)

(defn make-db-handler [db] 
	(let [wrap-db (make-wrap-db db)]
		(-> toronto-bids
;			(debug/wrap-with-logger)
			(wrap-db)
			(params/wrap-params)
		)
	)
)

(defn -main
  "toronto-bids server, Thin wrapper over database reads"
  [& args]
  	(if (== 2 (count args))
		(let 
			[
				db-name (first args)
				portString (second args)

				db (stuff/db-spec db-name)
			]
			(try
				(let [port (Integer/parseInt portString)]
					(println (str "Will run on port " portString))
					(ring/run-jetty (make-db-handler db) {:port port})
				)
				(catch NumberFormatException exception 
					(println (str portString " is not an int"))
				)
			)
		)  	
		(println "toronto-bids db-name port")
	)
)
