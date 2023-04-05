(ns frontend.details
	(:gen-class)
	(:require [clojure.string :as str])
	(:require [clojure.data.json :as json])
	(:require [hiccup.core :as hiccup])
	(:require [hiccup.page :as page])
	(:require [hiccup.form :as form])
;	(:require [frontend.filter] :as filter)
)

(defn body [api-base document_id]
	[:div "Document details"]
)

(defn output [api-base local-base document_id]
	(page/html5 [:body (body api-base document_id)])
)


