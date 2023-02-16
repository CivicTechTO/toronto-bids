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
;	 (:require [ring-debug-logging.core :as debug])
)

(defn write-simple [table columns]
		(let 
			 [
				  query (str "SELECT " columns " FROM " table)
			 ]
			 (json/write-str (jdbc/query stuff/DB-SPEC [query]))
		)
)

(compojure/defroutes toronto-bids
		(compojure/GET "/toronto-bids/api/types" [] (write-simple "type" "id, type"))
		(compojure/GET "/toronto-bids/api/commodities" [] (write-simple "commodity" "id, commodity"))
		(compojure/GET "/toronto-bids/api/commodity_types" [] (write-simple "commodity_type" "id, commodity_id, commodity_type"))
		(compojure/GET "/toronto-bids/api/divisions" [] (write-simple "division" "id,division"))
		(compojure/GET "/toronto-bids/api/buyers" [] (write-simple "buyer" "id, name"))

		(compojure-route/not-found "Not found")
)

(def handler 
	(-> toronto-bids
;		(debug/wrap-with-logger)
		(params/wrap-params)
	)
)

(defn -main
  "toronto-bids server, Thin wrapper over database reads"
  [& args]
		(println (str "Will run on port " stuff/PORT))
		(ring/run-jetty handler {:port stuff/PORT})
)
