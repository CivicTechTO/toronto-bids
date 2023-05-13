(ns toronto-bids.core
	(:gen-class)

	(:require [ring.adapter.jetty :as jetty])
	(:require [ring.util.response :as response])
	(:require [ring.middleware.params :as params])
	(:require [ring.middleware.cors :as cors])
	(:require [ring.middleware.json :as json])
	(:require [ring.middleware.content-type :as content-type])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as compojure-route])
	(:require [clojure.java.jdbc :as jdbc])
	(:require [toronto-bids.stuff :as stuff])
	(:require [toronto-bids.documents :as documents])
;	(:require [ring-debug-logging.core :as debug])
)

(defn output-simple [db table columns]
	(let 
		[
		  query (str "SELECT " columns " FROM " table)
		]
		(response/response (jdbc/query db [query]))
	)
)

(defn make-extract [column]
	(fn [row]
		(get row (keyword column))
	)
)

(defn output-plain [db table column]
	(let 
		[
			extract (make-extract column)
		  query (str "SELECT " column " FROM " table)
		  result (jdbc/query db [query])
		]
		(response/response (map extract result))
	)
)

(compojure/defroutes toronto-bids
	(compojure/GET "*/api/types.json" [db] (output-simple db "type" "id, type"))
	(compojure/GET "*/api/commodities.json" [db] (output-simple db "commodity" "id, commodity"))
	(compojure/GET "*/api/commodity_types.json" [db] (output-simple db "commodity_type" "id, commodity_id, commodity_type"))
	(compojure/GET "*/api/divisions.json" [db] (output-simple db "division" "id,division"))
	(compojure/GET "*/api/buyers.json" [db] (output-simple db "buyer" "id, buyer"))

	(compojure/GET "*/api/plain_divisions.json" [db] (output-plain db "division" "division"))
	(compojure/GET "*/api/plain_types.json" [db] (output-plain db "type" "type"))
	(compojure/GET "*/api/plain_commodities.json" [db] (output-plain db "commodity" "commodity"))
	(compojure/GET "*/api/plain_commodity_types.json" [db] (output-plain db "commodity_type" "commodity_type"))
	(compojure/GET "*/api/plain_buyers.json" [db] (output-plain db "buyer" "buyer"))

	(compojure/GET "*/api/documents.json" 
		[db call_number type division commodity commodity_type posting_date_after posting_date_before closing_date_after closing_date_before buyer search_text limit offset] 
		(documents/output-documents db 
			(list call_number type division commodity commodity_type posting_date_after posting_date_before closing_date_after closing_date_before buyer search_text) 
			limit offset
		)
	)

	(compojure/GET "*/api/description.json" [db document_id] (documents/output-description db document_id))

	(compojure-route/not-found (list "End point not found"))
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
			(cors/wrap-cors :access-control-allow-origin [#".*"] :access-control-allow-methods [:get])
			(json/wrap-json-response)
			(content-type/wrap-content-type)
;			(debug/wrap-with-logger)
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
					(jetty/run-jetty (make-db-handler db) {:port port})
				)
				(catch NumberFormatException exception 
					(println (str portString " is not an int"))
				)
			)
		)  	
		(println "toronto-bids db-name port")
	)
)
