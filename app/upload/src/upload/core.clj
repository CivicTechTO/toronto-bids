(ns upload.core
	(:gen-class)

	(:require [clojure.string :as str])
	(:require [ring.adapter.jetty :as ring])
	(:require [ring.middleware.params :as params])
	(:require [compojure.core :as compojure])
	(:require [compojure.route :as compojure-route])
	(:require [clojure.java.jdbc :as jdbc])
	(:require [clojure.data.json :as json])
	(:require [upload.stuff :as stuff])
	(:require [ring-debug-logging.core :as debug])
)

(defn upload-call [db call]
	"Success"
)

(compojure/defroutes bids-upload
	(compojure/POST "*/upload/call" [db call] (upload-call db call))

	(compojure-route/not-found (json/write-str "End point not found"))
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
		(-> bids-upload
			(debug/wrap-with-logger)
			(wrap-db)
			(params/wrap-params)
		)
	)
)

(defn -main
  "toronto-bids upload, Thin wrapper over database writes"
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
					(ring/run-jetty (make-db-handler db) {:port port})
				)
				(catch NumberFormatException exception 
					(println (str portString " is not an int"))
				)
			)
		)  	
		(println "upload db-name port")
	)
)
